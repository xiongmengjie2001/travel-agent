/**
 * 前端共享类型（对应 docs/module-sdd/frontend.md §3）。
 *
 * 后端契约字段以 `src/models/` 为准，本文件只做 TypeScript 映射，不添加业务规则。
 */

/** 后端统一响应包裹（docs/schemas/api-response.md）。 */
export interface ApiResponse<T> {
  code: number
  msg: string
  data: T
}

/** 单日天气；温度缺失时为 null。 */
export interface WeatherInfo {
  date: string
  condition: string
  temp_high: number | null
  temp_low: number | null
}

/** 景点，列表顺序即游览顺序。 */
export interface Spot {
  name: string
  duration_min: number
  ticket_price: number
}

/** 单日行程。 */
export interface DailyPlan {
  day_index: number
  date: string | null
  spots: Spot[]
  estimated_cost: number
  weather: WeatherInfo | null
}

/** 花费四段分项。 */
export interface CostResult {
  days: number
  tickets_total: number
  transport_total: number
  lodging_total: number
  food_total: number
  total: number
  within_budget: boolean | null
}

/** 行程来源标识。 */
export type TripSource = 'llm' | 'template' | 'unknown'

/** 完整行程方案（TripRecord.itinerary）。 */
export interface TripPlan {
  destination: string
  days: number
  budget: number | null
  total_cost: number
  daily: DailyPlan[]
  degraded: boolean
  weather_report: WeatherInfo[]
  cost_breakdown: CostResult | null
  source: TripSource
}

/** 前端行程卡片，Bridge.to_trip_card() 的产出。 */
export interface TripCard {
  trip_id: string
  destination: string
  days: number
  total_cost: number
  degraded: boolean
  daily: DailyPlan[]
  weather_report: WeatherInfo[]
  cost_breakdown: CostResult | null
  source: TripSource
}

/** 单条完整行程记录（GET /api/trips/{trip_id}）。 */
export interface TripRecord {
  trip_id: string
  created_at: string
  destination: string
  days: number
  budget: number | null
  intent_raw: string
  itinerary: TripPlan
  degraded: boolean
}

/** 后端动作常量。 */
export type AgentAction = 'plan_trip' | 'list_trips' | 'search_trips' | 'delete_trip' | 'chat'

/** POST /api/chat 的 data。 */
export interface AgentResult {
  action: AgentAction
  message: string
  trip: TripCard | null
  trips: TripCard[]
}

/** DELETE /api/trips/{trip_id} 的 data。 */
export interface DeleteTripResult {
  trip_id: string
  deleted: boolean
}

/** 对话气泡角色。error 是第三类气泡，用于业务 / 网络失败。 */
export type ChatRole = 'user' | 'assistant' | 'error'

/** 前端内部消息，不上行传给后端。 */
export interface ChatMessage {
  id: string
  role: ChatRole
  text: string
  trip: TripCard | null
  trips: TripCard[]
}

/** 错误分类：后端业务错误 / 网络不可达 / 请求超时。 */
export type ApiErrorKind = 'business' | 'network' | 'timeout'

/** 归一化后的错误对象，组件只读这三个字段。 */
export interface AppError {
  code: number
  message: string
  kind: ApiErrorKind
}

/** 主题模式。 */
export type ThemeMode = 'light' | 'dark' | 'system'

/** 本地配置，持久化到 localStorage。 */
export interface AppConfig {
  theme: ThemeMode
  apiKey: string
  model: string
  /** 后端地址；空串表示走开发服务器的 `/api` 代理。 */
  apiBaseUrl: string
}

/** Toast 类型。 */
export type ToastType = 'info' | 'success' | 'warning' | 'error'

/** Toast 队列元素。 */
export interface ToastItem {
  id: number
  type: ToastType
  text: string
  duration: number
}

/** 横幅重试动作：重发消息 / 刷新历史 / 重试删除。 */
export type BannerRetry = 'chat' | 'history' | 'delete'

/** 顶部横幅。 */
export interface Banner {
  type: 'info' | 'warning' | 'error'
  text: string
  /** 自动消失毫秒数；缺省或 0 表示常驻，需手动关闭。 */
  duration?: number
  /** 网络失败时的重试入口；缺省表示不提供重试。 */
  retry?: BannerRetry | null
}
