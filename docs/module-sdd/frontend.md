# Frontend SDD

> 文件：`frontend/`（Vue 3 单页应用）
> 版本：v3.0（增强行程卡片 + 反馈系统 + 主题与配置，2026-10-21）
> 前版：v2.0 / 2026-10-21（TypeScript + Pinia + 三栏布局）；v1.0 / 2026-10-03（JavaScript + 组件内状态）
> 依赖契约：[`docs/schemas/api-response.md`](../schemas/api-response.md)、[`docs/schemas/chat_request.json`](../schemas/chat_request.json)、[`docs/schemas/trip_response.json`](../schemas/trip_response.json)、[`system-design.md` §3](../system-design.md)

## 0. v3.0 变更

本次在 v2.0 的 TypeScript + Pinia + 三栏布局之上补齐展示与反馈能力，
**不改变后端契约与既有业务行为**：

1. `TripCard` 增加「模板降级」来源徽标，保留逐日折叠面板与降级说明；
2. 新增 `WeatherForecast`，渲染 `TripCard.weather_report` 的日期 / 天气 / 温度区间；
3. 新增 `CostBreakdown`，渲染 `TripCard.cost_breakdown` 的门票 / 市内交通 / 住宿 / 餐饮 / 总计；
4. 新增 `ToastStack`，Toast 队列最多同时 5 条，支持成功 / 错误 / 警告 / 信息四类，自动或手动关闭；
5. 新增 `BannerBar`：网络失败显示红色横幅并提供重试，模板降级显示黄色横幅并自动消失；
6. 错误反馈细分：超时只弹 Toast；其他网络错误同时弹 Toast 与红色横幅；
7. 配置新增 `apiBaseUrl`（后端地址），与主题 / API Key / 模型一起持久化到 localStorage；
8. API 层在请求拦截器中读取本地配置，使后端地址可即时切换；
9. 新增 `src/config/storage.ts`，集中配置读写，避免 API 层反向依赖 store。

## 1. 功能描述

旅行规划智能体的浏览器端。**只负责交互与渲染**，不含业务规则：意图识别、行程编排、持久化全在后端（system-design.md §3「Frontend → API Layer」单向依赖）。

- 提供对话入口，用户输入与出发日期原样 POST 给 `/api/chat`；
- 渲染行程卡片：逐日折叠面板、多日天气、分项花费、模板降级徽标；
- 启动时拉取历史行程并在侧边栏展示（FR-2）；
- 点击侧边栏条目加载单条完整详情（`GET /api/trips/{trip_id}`）到主对话区；
- 顶部栏提供主题切换与设置入口；
- 用 Toast 与顶部横幅统一承载成功 / 警告 / 错误反馈；
- 把后端的 `code` / `degraded` / `source` 如实呈现（AC-5）。

前端**不判断**用户想干什么。动作识别唯一来源是后端返回的 `AgentResult.action`；前端只按该字段分发渲染，不做关键词匹配。

## 2. 技术栈

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| `vue` | `^3.5.42` | 视图层 |
| `pinia` | `^4.x` | 集中式状态管理 |
| `typescript` | `^5.x` | 类型检查 |
| `vue-tsc` | `^3.x` | Vue SFC 类型检查 |
| `vite` + `@vitejs/plugin-vue` | `^8.3.0` / `^6.0.8` | 构建与开发服务器 |
| `axios` | `^1.x` | HTTP 客户端 |

`npm run build` 先执行 `vue-tsc --noEmit` 再做 `vite build`，类型错误会阻断构建。

### 2.1 目录结构

```
frontend/
├── tsconfig.json
├── vite.config.ts
├── index.html
└── src/
    ├── main.ts              # createApp + createPinia
    ├── env.d.ts             # vite/client 类型引用
    ├── types/index.ts       # 所有共享类型
    ├── config/storage.ts    # 配置读写 / 校验 / 后端地址规范化
    ├── api/client.ts        # axios 实例与四个接口
    ├── stores/config.ts     # 主题 / API Key / 模型 / 后端地址
    ├── stores/chat.ts       # 消息 / 行程 / 历史 / 错误 / Toast / 横幅
    ├── App.vue              # 三栏组装与事件接线
    └── components/
        ├── AppHeader.vue        # Logo / 标题 / 主题 / 设置
        ├── BannerBar.vue        # 顶部横幅（错误重试 / 降级自动消失）
        ├── HistorySidebar.vue   # 历史列表 / 详情入口 / 删除 / 刷新
        ├── ChatPanel.vue        # 主对话区组装
        ├── MessageList.vue      # 自动滚动 + 角色分派
        ├── UserMessage.vue      # 用户气泡
        ├── AssistantMessage.vue # 助手 / 错误气泡
        ├── TypingIndicator.vue  # 打字指示动画
        ├── ChatInput.vue        # 输入 / 快捷提问 / 出发日期
        ├── TripCard.vue         # 行程卡片 / 折叠日程 / 降级徽标
        ├── WeatherForecast.vue  # 多日天气
        ├── CostBreakdown.vue    # 分项花费
        ├── ToastStack.vue       # Toast 队列
        └── SettingsPanel.vue    # 设置弹层
```

## 3. 类型定义（`src/types/index.ts`）

| 类型 | 字段 | 说明 |
| --- | --- | --- |
| `ApiResponse<T>` | `code` / `msg` / `data` | 后端统一包裹 |
| `WeatherInfo` | `date` / `condition` / `temp_high` / `temp_low` | 温度可空 |
| `Spot` | `name` / `duration_min` / `ticket_price` | 景点 |
| `DailyPlan` | `day_index` / `date` / `spots` / `estimated_cost` / `weather` | `date`、`weather` 可空 |
| `CostResult` | `days` / `tickets_total` / `transport_total` / `lodging_total` / `food_total` / `total` / `within_budget` | 分项花费，`within_budget` 可空 |
| `TripSource` | `llm` / `template` / `unknown` | 行程来源 |
| `TripPlan` | `destination` / `days` / `budget` / `total_cost` / `daily` / `degraded` / `weather_report` / `cost_breakdown` / `source` | 完整方案 |
| `TripCard` | `trip_id` / `destination` / `days` / `total_cost` / `degraded` / `daily` / `weather_report` / `cost_breakdown` / `source` | 卡片契约 |
| `TripRecord` | `trip_id` / `created_at` / `destination` / `days` / `budget` / `intent_raw` / `itinerary` / `degraded` | 详情契约 |
| `AgentAction` | `plan_trip` / `list_trips` / `search_trips` / `delete_trip` / `chat` | 后端动作 |
| `AgentResult` | `action` / `message` / `trip` / `trips` | `trip` 与 `trips` 至多一个非空 |
| `DeleteTripResult` | `trip_id` / `deleted` | 删除接口返回 |
| `ChatMessage` | `id` / `role` / `text` / `trip` / `trips` | 前端内部消息，不上行 |
| `ApiErrorKind` | `business` / `network` / `timeout` | 错误分类 |
| `AppError` | `code` / `message` / `kind` | 归一化错误对象 |
| `ToastType` | `info` / `success` / `warning` / `error` | Toast 类型 |
| `ToastItem` | `id` / `type` / `text` / `duration` | Toast 队列元素 |
| `Banner` | `type` / `text` / `duration?` / `retry?` | 顶部横幅；`duration` 为自动消失毫秒数，`retry` 为 `chat` / `history` / `delete` |
| `ThemeMode` | `light` / `dark` / `system` | 主题 |
| `AppConfig` | `theme` / `apiKey` / `model` / `apiBaseUrl` | 持久化配置 |

`ChatMessage.role` 取 `user` / `assistant` / `error` 三种；`role === 'error'` 用于业务失败与网络失败，与正常助手回复区分。

## 4. API 层（`src/api/client.ts`）

```ts
const DEFAULT_TIMEOUT_MS = 10_000
const CHAT_TIMEOUT_MS = 60_000

sendMessage(text: string, startDate?: string | null): Promise<AgentResult>
getTrips(limit?: number): Promise<TripCard[]>
getTrip(tripId: string): Promise<TripRecord>
deleteTrip(tripId: string): Promise<DeleteTripResult>
```

| 方法 | 端点 | 超时 |
| --- | --- | --- |
| `sendMessage` | `POST /api/chat` | **60s**（AI 生成耗时长） |
| `getTrips` | `GET /api/trips?limit=` | 10s |
| `getTrip` | `GET /api/trips/{trip_id}` | 10s |
| `deleteTrip` | `DELETE /api/trips/{trip_id}` | 10s |

**响应处理**：

- 响应拦截器校验 `body.code` 为 number；`code === 0` 时 resolve `body.data`，否则 reject `ApiError(body.msg, body.code, 'business')`；
- HTTP 400 也按 body 的 `code` 判定（1001 是业务错误），不得按 HTTP 状态判定成功与否；
- `ECONNABORTED` / `ETIMEDOUT` → `ApiError(kind='timeout', code=-2)`；
- 无响应或响应不符契约 → `ApiError(kind='network', code=-1)`；
- 所有异常最终都是 `ApiError`，调用方只读 `code` / `message` / `kind`。

**后端地址解析**：

1. 请求拦截器在每次请求前读取 `src/config/storage.ts` 的 `readConfig().apiBaseUrl`，写入 `config.baseURL`；
2. 用户配置为空串时回落到 `VITE_API_BASE_URL ?? ''`，开发期即走 `vite.config.ts` 的 `/api` 代理；
3. 后端地址只允许来自环境变量或用户在设置面板中的输入，源码中不得出现地址字面量；
4. 读取时去除首尾空白与末尾 `/`，避免拼接出双斜杠；输入过程中不裁剪，避免影响正在编辑的文本。

## 5. 状态契约（Pinia）

### 5.1 配置 store（`useConfigStore`，id `config`）

| 状态 | 类型 | 默认值 |
| --- | --- | --- |
| `theme` | `ThemeMode` | `light` |
| `apiKey` | `string` | `''` |
| `model` | `string` | `deepseek-v4-flash` |
| `apiBaseUrl` | `string` | `VITE_API_BASE_URL ?? ''` |

- localStorage key：`travel-agent2:config`；
- 读取时逐字段校验，损坏或非法值回落到默认值；`localStorage` 不可用时静默回退，不影响渲染；
- 每次 setter 调用后**同步写回**；`localStorage` 写入失败只记 `console.warn`，不抛出；
- **API Key 仅存本机浏览器**，不上行、不写入源码 / 仓库。

| 动作 | 签名 | 行为 |
| --- | --- | --- |
| `setTheme` | `(value: ThemeMode) => void` | 赋值并同步 `document.documentElement.dataset.theme` |
| `toggleTheme` | `() => void` | 在 `light` / `dark` 之间切换 |
| `setApiKey` | `(value: string) => void` | 赋值 |
| `setModel` | `(value: string) => void` | 去空白后赋值，空串回落默认模型 |
| `setApiBaseUrl` | `(value: string) => void` | 赋值并持久化；请求层读取时去除首尾空白与末尾 `/` |
| `reset` | `() => void` | 恢复默认、同步主题并清除持久化 |

### 5.2 对话 store（`useChatStore`，id `chat`）

**状态**

| 状态 | 类型 | 说明 |
| --- | --- | --- |
| `messages` | `ChatMessage[]` | 对话消息，含用户 / 助手 / 错误气泡 |
| `loading` | `boolean` | 请求进行中，兼作输入框禁用条件 |
| `currentTrip` | `TripCard \| null` | 最近一次 `plan_trip` 的结果 |
| `history` | `TripCard[]` | 历史行程列表 |
| `historyLoading` | `boolean` | 历史列表加载 / 刷新中 |
| `historyError` | `AppError \| null` | 历史列表专用错误（不与对话错误混用） |
| `historyDetail` | `TripRecord \| null` | 最近加载的单条详情 |
| `detailLoading` | `boolean` | 单条详情加载中 |
| `activeTripId` | `string \| null` | 侧边栏当前选中的行程 |
| `error` | `AppError \| null` | 最近一次错误 |
| `toasts` | `ToastItem[]` | Toast 队列，**最多同时 5 条** |
| `banner` | `Banner \| null` | 顶部横幅 |
| `lastSentText` | `string` | 最后一次发送的文本 |
| `lastStartDate` | `string \| null` | 最后一次发送的出发日期 |

**动作**

| 动作 | 签名 | 行为 |
| --- | --- | --- |
| `send` | `(text: string, startDate?: string \| null) => Promise<void>` | 去空白；空文本或 loading 中直接返回。记录 lastSentText / lastStartDate，推送用户气泡，调用 `sendMessage` |
| `retry` | `() => Promise<void>` | 用 `lastSentText` 重发，**不重复推送用户气泡**；无文本或 loading 中直接返回 |
| `retryBanner` | `() => void` | 按 `banner.retry` 分派：`chat` 调 `retry()`，`history` 调 `refreshHistory(true)`，`delete` 重试最近一次删除；无重试动作时忽略 |
| `loadHistory` | `() => Promise<void>` | 启动时拉列表，写入 `history`，推送「已加载 N 条 / 暂无」气泡 |
| `refreshHistory` | `(announce?: boolean) => Promise<void>` | 静默刷新 `history`；`announce` 为真时推送结果气泡 |
| `loadTripDetail` | `(tripId: string) => Promise<TripRecord \| null>` | 拉取单条详情并写入 `historyDetail`；失败返回 `null` |
| `openHistoryTrip` | `(tripId: string) => Promise<TripRecord \| null>` | 侧边栏点击：加载详情、设置 `currentTrip`，并推送一张助手卡片 |
| `deleteTrip` | `(tripId: string) => Promise<boolean>` | 调删除接口；成功或 3001 时从所有气泡、`history`、`currentTrip`、`historyDetail` 中移除；成功弹成功 Toast，失败推送错误气泡 |
| `handleError` | `(input: unknown, context?: 'chat' \| 'history' \| 'delete' \| 'general') => AppError` | 归一化、写入 `error`、推送错误气泡，并按 §5.3 弹 Toast / 横幅 |
| `addToast` | `(text: string, type?: ToastType, duration?: number) => number` | 入队并返回 id；超过 5 条时淘汰最早一条；`duration > 0` 时定时自动移除，`0` 表示仅手动关闭 |
| `removeToast` | `(id: number) => void` | 清除定时器并从队列移除 |
| `setBanner` | `(banner: Banner \| null) => void` | 设置横幅；传 `null` 等价清除 |
| `clearBanner` | `() => void` | 清除横幅 |

**发送结果分发（只按后端 `action` 分派）**

| `action` | 处理 |
| --- | --- |
| `plan_trip` | 推送助手气泡 + `trip`；`currentTrip = trip`；有 `trip_id` 时前插到 `history`；降级时设置黄色横幅（见 §5.4） |
| `list_trips` | 推送助手气泡 + `trips`；`history = trips` |
| `search_trips` | 推送助手气泡 + `trips`；**不覆盖 `history`**（检索结果是子集） |
| `delete_trip` | 推送助手气泡；静默刷新 `history` |
| `chat` / 未知动作 | 推送助手气泡（`text` / `trips` 按实际字段） |

### 5.3 错误反馈规范

| `kind` | 上下文 | 错误气泡文案 | Toast | 顶部横幅 |
| --- | --- | --- | --- | --- |
| `business` | 任意 | 后端 `msg` 原样 | `error`，5s | 无 |
| `timeout` | `chat` | 「请求超时：AI 生成可能仍在后台进行，请稍后重试」 | `warning`，5s | **无** |
| `timeout` | 其他 | 「请求超时，请稍后重试」 | `warning`，5s | **无** |
| `network` | `chat` | 「无法连接后端服务，请确认后端已启动」 | `error`，5s | 红色 `error`，`retry: 'chat'` |
| `network` | `history` | 同上 | `error`，5s | 红色 `error`，`retry: 'history'` |
| `network` | `delete` | 同上 | `error`，5s | 红色 `error`，`retry: 'delete'` |
| `network` | `general` | 同上 | `error`，5s | 红色 `error`，无重试 |

- 除 Toast / 横幅外，错误仍写入一条 `role === 'error'` 气泡，保留在对话流中；
- 请求成功时清除 `error` 与横幅；
- 超时与网络错误不混用：**超时不得出现红色横幅**。

### 5.4 降级提示

`plan_trip` 返回的 `trip` 满足 `degraded === true` 或 `source === 'template'` 时：

- 卡片头部展示「模板降级」徽标，卡片内保留降级说明；
- 状态层设置黄色横幅：`{ type: 'warning', text: '大模型暂不可用，已切换为模板降级方案', duration: 5000 }`；
- 横幅由 `BannerBar` 在 5 秒后自动关闭，用户也可手动关闭；
- `degraded === true` **不算错误**，不弹错误 Toast、不写错误气泡。

## 6. 组件树与契约

### 6.1 结构图

```
App.vue（组装 + 事件接线）
├── AppHeader.vue            props: theme
│                            emits: toggle-theme / open-settings
├── BannerBar.vue            props: banner
│                            emits: dismiss / retry
├── HistorySidebar.vue       props: trips / activeTripId / loading /
│                                   detailLoading / errorMessage
│                            emits: select / delete / refresh
├── ChatPanel.vue            props: messages / loading
│   ├── MessageList.vue      props: messages / loading
│   │   ├── UserMessage.vue          props: text
│   │   ├── AssistantMessage.vue     props: message
│   │   │   └── TripCard.vue         props: trip
│   │   │       ├── WeatherForecast.vue  props: report
│   │   │       └── CostBreakdown.vue    props: cost
│   │   └── TypingIndicator.vue      —
│   └── ChatInput.vue        props: disabled
│                            emits: send(text, startDate)
├── SettingsPanel.vue        props: open / theme / apiKey / model / apiBaseUrl
│                            emits: close / update:theme / update:apiKey /
│                                   update:model / update:apiBaseUrl
└── ToastStack.vue           props: toasts
                             emits: dismiss(id)
```

### 6.2 Props / Emits

| 组件 | props | emits | 职责 |
| --- | --- | --- | --- |
| `App.vue` | — | — | 从 store 读状态，接线上下行事件；`onMounted` 调 `loadHistory` |
| `AppHeader.vue` | `theme: ThemeMode` | `toggle-theme()`、`open-settings()` | Logo、标题、主题切换、设置入口 |
| `BannerBar.vue` | `banner: Banner \| null` | `dismiss()`、`retry()` | 红 / 黄 / 信息横幅；`duration > 0` 时自动 dismiss |
| `HistorySidebar.vue` | `trips: TripCard[]`、`activeTripId?: string \| null`、`loading?: boolean`、`detailLoading?: boolean`、`errorMessage?: string \| null` | `select(tripId)`、`delete(tripId)`、`refresh()` | 概要列表、选中态、空态 / 加载态、删除确认 |
| `ChatPanel.vue` | `messages: ChatMessage[]`、`loading?: boolean` | `send(text, startDate)`、`delete-trip(tripId)` | 主对话区组装 |
| `MessageList.vue` | `messages: ChatMessage[]`、`loading?: boolean` | `delete-trip(tripId)` | 自动滚动到底部；按 role 分派气泡；loading 时渲染打字指示 |
| `UserMessage.vue` | `text: string` | — | 用户气泡（右侧） |
| `AssistantMessage.vue` | `message: ChatMessage` | `delete-trip(tripId)` | 助手 / 错误气泡（左侧），承载单张或一组 `TripCard` |
| `TypingIndicator.vue` | — | — | 三点跳动动画 |
| `ChatInput.vue` | `disabled?: boolean = false` | `send(text: string, startDate: string \| null)` | 输入、快捷提问、出发日期、回车发送 |
| `TripCard.vue` | `trip: TripCard` | `delete(tripId)` | 折叠日程、模板降级徽标、降级说明、天气与花费区块、删除确认 |
| `WeatherForecast.vue` | `report: WeatherInfo[]` | — | 多日天气列表；空数组时不渲染 |
| `CostBreakdown.vue` | `cost: CostResult \| null` | — | 四段分项 + 总计；`null` 时展示「暂无分项花费」 |
| `ToastStack.vue` | `toasts: ToastItem[]` | `dismiss(id: number)` | 右上角 Toast 队列，按类型着色，手动关闭 |
| `SettingsPanel.vue` | `open: boolean`、`theme`、`apiKey`、`model`、`apiBaseUrl` | `close()`、`update:theme(value)`、`update:apiKey(value)`、`update:model(value)`、`update:apiBaseUrl(value)` | 设置弹层；Esc / 遮罩 / 关闭按钮退出 |

### 6.3 页面组装与接线

| 交互 | 接线 |
| --- | --- |
| 顶部主题按钮 | `App` → `config.toggleTheme()` |
| 顶部设置按钮 | `App` 打开 `settingsOpen`；`SettingsPanel` 的 `update:*` → `config.setTheme / setApiKey / setModel / setApiBaseUrl` |
| 输入框发送 | `ChatInput` → `ChatPanel` → `App.handleSend` → `chat.send(text, startDate)` |
| 快捷提问 | `ChatInput.usePrompt` 直接 emit `send(text, startDate)` |
| 侧边栏选择 | `HistorySidebar.select` → `App.handleSelectTrip` → `chat.openHistoryTrip(tripId)` |
| 侧边栏删除 / 卡片删除 | → `App.handleDeleteTrip` → `chat.deleteTrip(tripId)` |
| 侧边栏刷新 | → `App.handleRefreshHistory` → `chat.refreshHistory(true)` |
| 横幅重试 | `BannerBar.retry` → `chat.retryBanner()` |
| 横幅关闭 | `BannerBar.dismiss` → `chat.clearBanner()` |
| Toast 关闭 | `ToastStack.dismiss` → `chat.removeToast(id)` |
| 启动 | `App.onMounted` → `chat.loadHistory()` |

组件内允许的本地状态仅限展示状态（输入草稿、出发日期、展开集合、设置弹层开关、横幅计时器）；
业务状态与 API 调用一律在 store。错误气泡、历史错误、Toast 与横幅均为状态驱动，
组件不自造文案。

### 6.4 增强版行程卡片契约

`TripCard` 只接收后端裁好的字段，所有区块按字段是否存在决定渲染：

| 区块 | 数据来源 | 展示规则 |
| --- | --- | --- |
| 头部 | `destination` / `days` / `total_cost` / `source` | 标题、天数、预估总价；`source === 'template'` 时显示「模板降级」徽标 |
| 降级说明 | `degraded` | 为 `true` 时显示黄色说明条，不算错误 |
| 逐日折叠 | `daily[]` | 默认展开第 1 天；点击头部展开 / 收起；展开后展示景点与时长；无日程时显示「暂无逐日日程」 |
| 多日天气 | `weather_report[]` | 非空时逐条显示日期（`MM-DD`，`title` 保留完整日期）、天气状况、温度区间；温度缺失按可用字段展示 |
| 分项花费 | `cost_breakdown` | 非空时显示门票 / 市内交通 / 住宿 / 餐饮 / 总计；`within_budget` 非空时追加「在预算内 / 超出预算」标签；`null` 时显示「暂无分项花费」 |
| 删除 | `trip_id` | 非空时显示删除按钮，点击需二次确认 |

## 7. 关键交互流程

### 7.1 发送消息

1. `ChatInput` 校验非空且非 loading，emit `send(text, startDate)`；
2. `App.handleSend` 调 `chat.send`；store 记录最后输入、推送用户气泡、置 `loading = true`；
3. `client.sendMessage` 以 60s 超时 POST `/api/chat`；请求拦截器应用当前 `apiBaseUrl`；
4. 成功：清除错误 / 横幅，按 `AgentResult.action` 分发；`plan_trip` 落地卡片、更新 `currentTrip` 与历史，必要时设置降级横幅；
5. 失败：`handleError` 归一化错误，按 §5.3 写错误气泡、Toast 与红色横幅；
6. `loading = false`，输入框恢复可用。

### 7.2 加载历史

1. `App.onMounted` 调 `chat.loadHistory`；
2. `getTrips` 成功后写入 `history`，侧边栏渲染概要；空列表展示空态；
3. 失败写入 `historyError`，侧边栏展示错误态；网络失败额外弹 Toast 与带「刷新历史」的红色横幅；
4. 用户点击条目：`openHistoryTrip` → `getTrip` → 完整详情转成 `TripCard` 推入主对话区；
5. 删除 / 刷新沿用 v2.0 行为，删除成功额外弹成功 Toast。

### 7.3 降级提示

1. 后端返回 `degraded === true` 或 `source === 'template'`；
2. 卡片头部显示「模板降级」徽标，卡片内保留降级说明；
3. store 设置 5s 自动消失的黄色横幅；
4. 用户可手动关闭；后续成功请求会清除横幅。

### 7.4 网络错误与重试

1. 非超时的网络失败进入 `handleError('network', context)`；
2. 弹 5s 错误 Toast，并设置红色横幅；`chat` / `history` / `delete` 上下文分别带「重试发送」/「刷新历史」/「重试删除」；
3. 点击重试：`retryBanner` 清除横幅并执行对应动作，不会重复推送用户气泡；
4. 超时只弹警告 Toast，不设置红色横幅。

## 8. 主题方案

- 颜色全部走 CSS 变量（`src/style.css`），组件内不写死颜色值；
- 浅色为 `:root` 默认值；深色为 `:root[data-theme='dark']`；
- 未写 `data-theme` 时表示「跟随系统」，由 `prefers-color-scheme: dark` 接管；
- 新增成功色变量 `--success` / `--success-text` / `--success-surface` / `--success-border`，用于成功 Toast；
- `config.setTheme` 写入 `document.documentElement.dataset.theme`，并持久化到 localStorage；
- 顶部栏按钮在亮 / 暗之间切换，设置面板可选「跟随系统」。

## 9. 设置面板

| 字段 | 控件 | 持久化 | 说明 |
| --- | --- | --- | --- |
| 主题 | `select` | `theme` | 浅色 / 深色 / 跟随系统 |
| API Key | `password` | `apiKey` | 仅存本机浏览器，不上行 |
| 模型 | `text` | `model` | 默认 `deepseek-v4-flash` |
| 后端地址 | `text` | `apiBaseUrl` | 留空走 `/api` 代理；填写后由请求拦截器即时应用 |

- 配置写入 `travel-agent2:config`，刷新后保留；
- 清除 localStorage 或损坏 JSON 时整体回落默认值，页面不报错；
- 后端实际使用的 DeepSeek Key 仍由服务端环境变量决定。

## 10. 验收

| # | 验收项 | 判定 |
| --- | --- | --- |
| 1 | `npm run build` 成功（含 `vue-tsc --noEmit`） | 退出码 0 |
| 2 | 全部 `.js` 业务源码迁移为 `.ts`，无残留 `client.js` / `main.js` / `vite.config.js` | 文件不存在 |
| 3 | 四个 API 方法齐全，对话超时 60s、其余 10s | 代码核对 |
| 4 | 响应拦截器只按 `body.code` 判成功，错误归一化为 `ApiError` | 代码核对 |
| 5 | `App.vue` 不 import `api/client`，不持有业务状态 | 源码核对 |
| 6 | 前端不实现意图识别（无关键词动作判断） | 源码核对 |
| 7 | 三栏布局齐全：顶部栏、历史侧边栏、主对话区 | 页面可见 |
| 8 | 侧边栏点击条目能加载完整详情并渲染卡片 | 手动联调 |
| 9 | 侧边栏删除 / 刷新 / 空态 / 加载态均有展示 | 手动联调 |
| 10 | 消息列表在新消息与 loading 变化时自动滚到底部 | 手动联调 |
| 11 | 请求中显示打字指示、输入框与发送按钮禁用 | 手动联调 |
| 12 | 快捷提问一键发送；出发日期随 `/api/chat` 的 `start_date` 上行 | Network 面板核对 |
| 13 | 逐日安排可展开 / 收起，默认展开第 1 天 | 手动联调 |
| 14 | `source === 'template'` 时卡片头部展示「模板降级」徽标 | 手动联调 |
| 15 | 多日天气逐条渲染日期、天气状况、温度区间 | 手动联调 |
| 16 | 分项花费渲染门票 / 市内交通 / 住宿 / 餐饮 / 总计 | 手动联调 |
| 17 | Toast 队列最多同时 5 条，超出淘汰最早一条 | 代码核对 + 手动联调 |
| 18 | Toast 支持手动关闭；`duration > 0` 时自动关闭 | 手动联调 |
| 19 | 超时只弹 Toast，不出现红色横幅 | 手动联调 |
| 20 | 网络失败弹 Toast + 红色横幅，重试可再次发起请求 | 手动联调 |
| 21 | 降级黄色横幅 5s 自动消失，也可手动关闭 | 手动联调 |
| 22 | 主题切换写入 `data-theme` 并即时生效，刷新后保留 | 手动联调 |
| 23 | 设置面板可配置主题 / API Key / 模型 / 后端地址，刷新后保留 | 手动联调 |
| 24 | 后端地址变更后，后续请求立即使用新地址 | Network 面板核对 |
