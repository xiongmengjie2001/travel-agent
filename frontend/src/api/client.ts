/**
 * 后端 API 客户端（对应 docs/module-sdd/frontend.md §4）。
 *
 * 统一封装三件事，组件层不必重复处理：
 *
 * 1. **剥壳** —— 响应拦截器只把 `data` 交给调用方；
 * 2. **判码** —— 后端把除 1001、5000 外的业务错误也放在 **HTTP 200** 里
 *    （docs/schemas/api-response.md §5），故只看 HTTP 状态会把「行程不存在」
 *    当成成功。**一律以 body.code 为准**；
 * 3. **归一化异常** —— 一律 reject `ApiError`，调用方只读 `code` / `message` / `kind`。
 */

import axios, { type AxiosRequestConfig } from 'axios'

import { readConfig } from '../config/storage'
import type {
  AgentResult,
  ApiErrorKind,
  ApiResponse,
  DeleteTripResult,
  TripCard,
  TripRecord,
} from '../types'

/** 普通请求默认上限；对话接口单独放宽。 */
export const DEFAULT_TIMEOUT_MS = 10_000
/** 对话接口上限：后端调用 DeepSeek 的超时是 30s，此处须留出余量。 */
export const CHAT_TIMEOUT_MS = 60_000

/** 网络不可达（负数，与后端正数错误码区分）。 */
export const NETWORK_ERROR_CODE = -1
/** 请求超时。 */
export const TIMEOUT_ERROR_CODE = -2

/**
 * 后端地址在每次请求前从本地配置读取（见请求拦截器），因此设置面板修改后
 * 立即生效。配置为空串时走 vite.config.ts 的 /api 代理；需要直连时可在
 * 设置面板或 `frontend/.env.local` 的 `VITE_API_BASE_URL` 中指定。
 */

/** 统一错误对象：业务错误、网络错误、超时错误共用一种形状。 */
export class ApiError extends Error {
  readonly code: number
  readonly kind: ApiErrorKind

  constructor(message: string, code: number, kind: ApiErrorKind) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.kind = kind
  }
}

/** 判定响应体是否为约定的 ApiResponse 外壳。 */
function isApiResponse(value: unknown): value is ApiResponse<unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { code?: unknown }).code === 'number'
  )
}

/**
 * 把任意异常收敛为 `ApiError`。
 *
 * 关键分支：**HTTP 400 也是业务错误**（code 1001，api-response.md §5），此时
 * `error.response` 里带着结构化响应体，必须优先取它，否则「请求参数校验失败」
 * 会被误报成「连不上后端」。
 */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error

  if (axios.isAxiosError(error)) {
    const body: unknown = error.response?.data
    if (isApiResponse(body)) {
      return new ApiError(body.msg || '请求失败', body.code, 'business')
    }
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
      return new ApiError('请求超时，请稍后重试', TIMEOUT_ERROR_CODE, 'timeout')
    }
    return new ApiError(
      '无法连接后端服务，请确认后端已启动',
      NETWORK_ERROR_CODE,
      'network',
    )
  }

  if (error instanceof Error) {
    return new ApiError(error.message, NETWORK_ERROR_CODE, 'network')
  }
  return new ApiError('未知错误，请稍后重试', NETWORK_ERROR_CODE, 'network')
}

const http = axios.create({
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
})

/** 每次请求应用最新的后端地址，用户在设置面板修改后无需刷新页面。 */
http.interceptors.request.use((config) => {
  config.baseURL = readConfig().apiBaseUrl
  return config
})

http.interceptors.response.use(
  (response) => {
    const body: unknown = response.data
    if (!isApiResponse(body)) {
      throw new ApiError(
        '后端响应格式不符合 ApiResponse 契约',
        NETWORK_ERROR_CODE,
        'network',
      )
    }
    if (body.code !== 0) {
      throw new ApiError(body.msg || '请求失败', body.code, 'business')
    }
    // 响应拦截器统一剥壳；下方 request<T>() 在类型上承接这个返回值。
    return body.data as never
  },
  (error: unknown) => Promise.reject(toApiError(error)),
)

/** 类型化请求包装：运行时由拦截器返回 `data`，这里只负责泛型标注。 */
function request<T>(config: AxiosRequestConfig): Promise<T> {
  // axios 的泛型仍假定返回 AxiosResponse；响应拦截器已把它换成业务 data。
  return http.request<unknown, T>(config) as unknown as Promise<T>
}

/**
 * 发送一条对话消息（POST /api/chat）。
 *
 * 用户输入**原样上行**，不在前端做任何意图判断 —— 动作识别是后端
 * `Bridge.detect_action()` 的职责（frontend.md §1）。
 *
 * @param text 用户输入，非空。
 * @param startDate 出发日期（ISO 8601，如 2026-10-25）；未选时传 null。
 * @throws ApiError 后端非 0 code、网络不可达或超时。
 */
export function sendMessage(text: string, startDate?: string | null): Promise<AgentResult> {
  return request<AgentResult>({
    method: 'POST',
    url: '/api/chat',
    data: startDate ? { message: text, start_date: startDate } : { message: text },
    timeout: CHAT_TIMEOUT_MS,
  })
}

/**
 * 拉取历史行程（GET /api/trips），按创建时间倒序。
 *
 * @param limit 条数上限，缺省由后端决定（DEFAULT_LIST_LIMIT）。
 */
export function getTrips(limit?: number): Promise<TripCard[]> {
  return request<TripCard[]>({
    method: 'GET',
    url: '/api/trips',
    params: limit ? { limit } : undefined,
  })
}

/**
 * 获取单条完整行程（GET /api/trips/{trip_id}）。
 *
 * 与列表接口的区别：返回 `TripRecord`，额外带 `created_at`、`intent_raw`
 * 与完整 `itinerary`。
 *
 * @throws ApiError 3001 行程不存在。
 */
export function getTrip(tripId: string): Promise<TripRecord> {
  return request<TripRecord>({
    method: 'GET',
    url: `/api/trips/${encodeURIComponent(tripId)}`,
  })
}

/**
 * 删除指定行程（DELETE /api/trips/{trip_id}）。
 *
 * @returns `{ trip_id, deleted }`；结果同时由「是否抛错」表达。
 * @throws ApiError 3001 行程不存在。
 */
export function deleteTrip(tripId: string): Promise<DeleteTripResult> {
  return request<DeleteTripResult>({
    method: 'DELETE',
    url: `/api/trips/${encodeURIComponent(tripId)}`,
  })
}

export default { sendMessage, getTrips, getTrip, deleteTrip, toApiError, ApiError }
