/**
 * 对话状态（对应 docs/module-sdd/frontend.md §5.2）。
 *
 * 全应用唯一的业务状态持有者：消息、加载态、当前行程、历史列表、错误、
 * Toast 队列、顶部横幅。组件只读状态并转发事件，不直接调用 API。
 *
 * ⚠️ 动作分发只依据后端返回的 `AgentResult.action`，前端不做任何关键词匹配。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  deleteTrip as deleteTripApi,
  getTrip,
  getTrips,
  sendMessage,
  toApiError,
} from '../api/client'
import type {
  AgentResult,
  AppError,
  Banner,
  BannerRetry,
  ChatMessage,
  ChatRole,
  ToastItem,
  ToastType,
  TripCard,
  TripRecord,
} from '../types'

interface MessageSeed {
  role: ChatRole
  text?: string
  trip?: TripCard | null
  trips?: TripCard[]
}

/** 同时可见的 Toast 上限；超出时淘汰最早一条。 */
export const MAX_TOASTS = 5
/** 普通 Toast 自动关闭时间。 */
export const TOAST_DURATION_MS = 3000
/** 错误 / 警告 Toast 留久一点，便于阅读。 */
export const ERROR_TOAST_DURATION_MS = 5000
/** 降级横幅自动消失时间。 */
export const DEGRADED_BANNER_DURATION_MS = 5000

/** 错误上下文决定红色横幅的重试动作。 */
type ErrorContext = 'chat' | 'history' | 'delete' | 'general'

/** TripRecord → TripCard，供历史详情在主对话区复用卡片渲染。 */
function toTripCard(record: TripRecord): TripCard {
  const plan = record.itinerary
  return {
    trip_id: record.trip_id,
    destination: plan.destination || record.destination,
    days: plan.days || record.days,
    total_cost: plan.total_cost,
    degraded: record.degraded,
    daily: plan.daily ?? [],
    weather_report: plan.weather_report ?? [],
    cost_breakdown: plan.cost_breakdown ?? null,
    source: plan.source ?? 'unknown',
  }
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const loading = ref(false)
  const currentTrip = ref<TripCard | null>(null)
  const history = ref<TripCard[]>([])
  const historyDetail = ref<TripRecord | null>(null)
  const historyLoading = ref(false)
  const historyError = ref<AppError | null>(null)
  const detailLoading = ref(false)
  const activeTripId = ref<string | null>(null)
  const error = ref<AppError | null>(null)
  const toasts = ref<ToastItem[]>([])
  const banner = ref<Banner | null>(null)
  const lastSentText = ref('')
  const lastStartDate = ref<string | null>(null)

  let messageSequence = 0
  let toastSequence = 0
  /** 记录 Toast 定时器，手动关闭或淘汰时一并清理，避免回调重复触发。 */
  const toastTimers = new Map<number, number>()
  /** 删除失败时可据此重试同一条行程。 */
  let lastDeleteTripId: string | null = null

  /** 追加一条消息；`id` 用自增序号，避免删除行程后下标变化导致 diff 错乱。 */
  function pushMessage(seed: MessageSeed): ChatMessage {
    const message: ChatMessage = {
      id: `m${++messageSequence}`,
      role: seed.role,
      text: seed.text ?? '',
      trip: seed.trip ?? null,
      trips: seed.trips ?? [],
    }
    messages.value.push(message)
    return message
  }

  /** 把某条行程从所有气泡、历史、当前行程与详情中摘掉。 */
  function dropTrip(tripId: string): void {
    for (const message of messages.value) {
      if (message.trip?.trip_id === tripId) message.trip = null
      if (message.trips.length) {
        message.trips = message.trips.filter((trip) => trip.trip_id !== tripId)
      }
    }
    history.value = history.value.filter((trip) => trip.trip_id !== tripId)
    if (currentTrip.value?.trip_id === tripId) currentTrip.value = null
    if (historyDetail.value?.trip_id === tripId) historyDetail.value = null
    if (activeTripId.value === tripId) activeTripId.value = null
  }

  /** 新生成的行程前插到历史列表（后端已按 created_at 倒序）。 */
  function rememberTrip(trip: TripCard): void {
    if (!trip.trip_id) return
    history.value = [trip, ...history.value.filter((item) => item.trip_id !== trip.trip_id)]
  }

  function setBanner(next: Banner | null): void {
    banner.value = next
  }

  function clearBanner(): void {
    banner.value = null
  }

  function clearToastTimer(id: number): void {
    const timer = toastTimers.get(id)
    if (timer !== undefined) {
      window.clearTimeout(timer)
      toastTimers.delete(id)
    }
  }

  function removeToast(id: number): void {
    clearToastTimer(id)
    toasts.value = toasts.value.filter((toast) => toast.id !== id)
  }

  function addToast(
    text: string,
    type: ToastType = 'info',
    duration = TOAST_DURATION_MS,
  ): number {
    const id = ++toastSequence
    const item: ToastItem = { id, type, text, duration: Math.max(0, duration) }
    toasts.value.push(item)

    // 只保留最近 5 条；被淘汰的 Toast 不能继续持有定时器。
    while (toasts.value.length > MAX_TOASTS) {
      const dropped = toasts.value.shift()
      if (dropped) clearToastTimer(dropped.id)
    }

    if (item.duration > 0) {
      const timer = window.setTimeout(() => removeToast(id), item.duration)
      toastTimers.set(id, timer)
    }
    return id
  }

    /** 把错误上下文映射为横幅重试动作；普通上下文不提供重试。 */
  function bannerRetryFor(context: ErrorContext): BannerRetry | null {
    if (context === 'chat') return 'chat'
    if (context === 'history') return 'history'
    if (context === 'delete') return 'delete'
    return null
  }

  /** 归一化错误、写入状态、推送错误气泡，并按类型弹 Toast / 设横幅。 */
  function handleError(input: unknown, context: ErrorContext = 'general'): AppError {
    const normalized = toApiError(input)
    const appError: AppError = {
      code: normalized.code,
      message: normalized.message,
      kind: normalized.kind,
    }
    error.value = appError
    clearBanner()

    let text = appError.message
    if (appError.kind === 'timeout') {
      text =
        context === 'chat'
          ? '请求超时：AI 生成可能仍在后台进行，请稍后重试'
          : '请求超时，请稍后重试'
      addToast(text, 'warning', ERROR_TOAST_DURATION_MS)
    } else if (appError.kind === 'network') {
      text = '无法连接后端服务，请确认后端已启动'
      setBanner({ type: 'error', text, retry: bannerRetryFor(context) })
      addToast(text, 'error', ERROR_TOAST_DURATION_MS)
    } else {
      addToast(text, 'error', ERROR_TOAST_DURATION_MS)
    }

    pushMessage({ role: 'error', text })
    return appError
  }

  /** 按后端 action 分发结果（frontend.md §5.2「发送结果分发」）。 */
  function applyResult(result: AgentResult): void {
    switch (result.action) {
      case 'plan_trip': {
        const trip = result.trip ?? null
        if (trip) {
          currentTrip.value = trip
          rememberTrip(trip)
          if (trip.trip_id) activeTripId.value = trip.trip_id

          // 降级方案不是错误：用黄色横幅提示，5 秒后由 BannerBar 自动关闭。
          if (trip.degraded || trip.source === 'template') {
            setBanner({
              type: 'warning',
              text: '大模型暂不可用，已切换为模板降级方案',
              duration: DEGRADED_BANNER_DURATION_MS,
            })
          }
        }
        pushMessage({ role: 'assistant', text: result.message, trip })
        break
      }
      case 'list_trips': {
        const trips = result.trips ?? []
        history.value = trips
        if (activeTripId.value && !trips.some((trip) => trip.trip_id === activeTripId.value)) {
          activeTripId.value = null
        }
        pushMessage({ role: 'assistant', text: result.message, trips })
        break
      }
      case 'search_trips': {
        // 检索结果是历史列表的子集，不能用它覆盖 sidebar。
        pushMessage({ role: 'assistant', text: result.message, trips: result.trips ?? [] })
        break
      }
      case 'delete_trip': {
        pushMessage({ role: 'assistant', text: result.message })
        void refreshHistory(false)
        break
      }
      default: {
        pushMessage({ role: 'assistant', text: result.message })
        break
      }
    }
  }

  /** 真正发起对话请求；重试与首次发送共用，避免重复推送用户气泡。 */
  async function requestSend(text: string, startDate: string | null): Promise<void> {
    loading.value = true
    try {
      const result = await sendMessage(text, startDate)
      error.value = null
      clearBanner()
      applyResult(result)
    } catch (cause) {
      handleError(cause, 'chat')
    } finally {
      loading.value = false
    }
  }

  async function send(text: string, startDate: string | null = null): Promise<void> {
    const trimmed = text.trim()
    if (!trimmed || loading.value) return

    lastSentText.value = trimmed
    lastStartDate.value = startDate
    error.value = null
    clearBanner()
    pushMessage({ role: 'user', text: trimmed })
    await requestSend(trimmed, startDate)
  }

  /** 重发最后一次输入；不重复推送用户气泡。 */
  async function retry(): Promise<void> {
    if (!lastSentText.value || loading.value) return
    error.value = null
    clearBanner()
    await requestSend(lastSentText.value, lastStartDate.value)
  }

  /** 红色横幅上的重试入口：按 store 记录的动作分派。 */
  function retryBanner(): void {
    const action = banner.value?.retry
    if (!action) return
    clearBanner()
    if (action === 'chat') {
      void retry()
    } else if (action === 'history') {
      void refreshHistory(true)
    } else if (lastDeleteTripId) {
      void deleteTrip(lastDeleteTripId)
    }
  }

  async function loadHistory(): Promise<void> {
    historyLoading.value = true
    try {
      const trips = await getTrips()
      history.value = trips
      historyError.value = null
      error.value = null
      clearBanner()
      pushMessage({
        role: 'assistant',
        text: trips.length
          ? `已加载 ${trips.length} 条历史行程。`
          : '还没有历史行程，输入出行需求开始规划。',
        trips,
      })
    } catch (cause) {
      historyError.value = handleError(cause, 'history')
    } finally {
      historyLoading.value = false
    }
  }

  async function refreshHistory(announce = false): Promise<void> {
    historyLoading.value = true
    try {
      const trips = await getTrips()
      history.value = trips
      historyError.value = null
      error.value = null
      clearBanner()
      if (announce) {
        pushMessage({
          role: 'assistant',
          text: trips.length ? `已刷新历史行程，共 ${trips.length} 条。` : '暂无历史行程。',
        })
      }
    } catch (cause) {
      historyError.value = handleError(cause, 'history')
    } finally {
      historyLoading.value = false
    }
  }

  /** 拉取单条详情并写入 `historyDetail`；失败返回 null。 */
  async function loadTripDetail(tripId: string): Promise<TripRecord | null> {
    if (!tripId || detailLoading.value) return null
    detailLoading.value = true
    error.value = null
    try {
      const record = await getTrip(tripId)
      historyDetail.value = record
      clearBanner()
      return record
    } catch (cause) {
      handleError(cause, 'history')
      return null
    } finally {
      detailLoading.value = false
    }
  }

  /** sidebar 点击：加载完整详情并作为助手气泡展示到主对话区。 */
  async function openHistoryTrip(tripId: string): Promise<TripRecord | null> {
    activeTripId.value = tripId
    const record = await loadTripDetail(tripId)
    if (!record) {
      activeTripId.value = null
      return null
    }
    const card = toTripCard(record)
    currentTrip.value = card
    pushMessage({
      role: 'assistant',
      text: `已加载「${record.destination}」的行程详情（${card.days} 天）。`,
      trip: card,
    })
    return record
  }

  async function deleteTrip(tripId: string): Promise<boolean> {
    lastDeleteTripId = tripId
    try {
      await deleteTripApi(tripId)
      dropTrip(tripId)
      clearBanner()
      addToast('行程已删除', 'success')
      return true
    } catch (cause) {
      const appError = handleError(cause, 'delete')
      // 3001 表示后端本就没有这条记录，留在界面上是脏数据；网络失败则保留。
      if (appError.code === 3001) dropTrip(tripId)
      return false
    }
  }

  return {
    // 状态
    messages,
    loading,
    currentTrip,
    history,
    historyDetail,
    historyLoading,
    historyError,
    detailLoading,
    activeTripId,
    error,
    toasts,
    banner,
    lastSentText,
    lastStartDate,
    // 动作
    send,
    retry,
    retryBanner,
    loadHistory,
    refreshHistory,
    loadTripDetail,
    openHistoryTrip,
    deleteTrip,
    handleError,
    addToast,
    removeToast,
    setBanner,
    clearBanner,
  }
})
