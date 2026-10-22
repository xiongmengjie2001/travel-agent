# Bridge SDD

> 文件：`src/bridge.py`

## 0. v4.0 变更（2026-10-21）

- 动作常量新增 `ACTION_GENERAL_CHAT = ACTION_CHAT` —— 一般对话与兜底 chat
  共用动作值，仅在语义上区分「显式问候 / 闲聊」；
- `to_trip_card()` 透传三个增强字段：`weather_report` / `cost_breakdown` /
  `source`，缺失时分别按逐日天气、总花费与 `degraded` 兜底；
- `parse_chat_request()` 支持可选的 `start_date`（ISO 日期），非法格式仍转换为
  `InvalidRequestError`（1001）。

## 1. 功能描述

前后端之间的数据格式适配层，同时承担用户输入的动作识别。负责把原始请求体转为内部模型、把内部结果转为统一响应格式与前端卡片格式。本模块不产生业务数据，不访问数据库。

## 2. 接口签名

```python
class Bridge:
    @staticmethod
    def parse_chat_request(payload: dict) -> ChatRequest: ...
    @staticmethod
    def to_api_response(data: Any = None, error: Exception | None = None) -> dict: ...
    @staticmethod
    def to_trip_card(plan: TripPlan, trip_id: str = "") -> TripCard: ...
    @staticmethod
    def detect_action(text: str) -> str: ...
```

| 方法 | 签名 | 含义 |
| --- | --- | --- |
| `parse_chat_request` | `(payload: dict) -> ChatRequest` | 原始请求体 → 内部模型；校验失败抛 `InvalidRequestError` |
| `to_api_response` | `(data: Any = None, error: Exception \| None = None) -> dict` | 组装统一响应体，**永不抛异常** |
| `to_trip_card` | `(plan: TripPlan, trip_id: str = "") -> TripCard` | 裁剪出前端卡片所需字段 |
| `detect_action` | `(text: str) -> str` | 识别动作，返回动作常量字符串 |

四个方法均为静态方法，`Bridge` 不需要实例状态。

> **`trip_id` 参数的补充说明（实现期修正，v1.0 → v1.1）**
>
> 原签名 `to_trip_card(plan: TripPlan)` 无处取得 `trip_id`：它由
> [MemoryStore](./memory.md) 落库时才生成，而 `TripPlan` 并不含该字段；
> `TripCard.trip_id` 却是前端删除 / 修改行程的唯一句柄（AC-2）。
> 故签名补充可选参数 `trip_id`，未落库的临时展示传空串（默认值）。
>
> 连带修正：下方字段映射表原先漏列 `trip_id` 一行，一并补上。

### 动作常量与识别规则

```python
ACTION_PLAN_TRIP = "plan_trip"
ACTION_LIST_TRIPS = "list_trips"
ACTION_SEARCH_TRIPS = "search_trips"
ACTION_DELETE_TRIP = "delete_trip"
ACTION_CHAT = "chat"
# 一般对话与兜底 chat 共用动作值，仅语义区分
ACTION_GENERAL_CHAT = ACTION_CHAT
```

`detect_action` 按**自上而下**顺序匹配，命中即停：

| 优先级 | 文本特征 | 返回 |
| --- | --- | --- |
| 1 | 含删除语义（删除 / 删掉 / 移除） | `delete_trip` |
| 2 | 含查看语义（查看 / 我的行程 / 历史） | `list_trips` |
| 3 | 含检索语义（搜索 / 查找） | `search_trips` |
| 4 | 含目的地或行程关键词（去 / 游 / 玩 / 规划 + 天数） | `plan_trip` |
| 5 | 以上均不匹配（问候 / 闲聊等一般对话） | `chat`（语义别名 `ACTION_GENERAL_CHAT`） |

优先级 1–3 必须高于 4 —— 「删除北京行程」同时含目的地与删除语义，应判为删除。返回值与 [Scheduler](./scheduler.md) 的分派表一一对应。

### `to_api_response` 的语义

| 入参情况 | 返回体 |
| --- | --- |
| `data` 非空，`error is None` | `{"code": 0, "msg": "ok", "data": <序列化后的 data>}` |
| `data` 为 `degraded=True` 的行程 | 同上，`code` 仍为 `0`，`data.degraded` 为 `true`（AC-5） |
| `error` 是 `AgentError` 子类 | `{"code": exc.code, "msg": exc.msg, "data": null}` |
| `error` 是其他异常 | `{"code": 5000, "msg": "内部错误", "data": null}` |
| `data` 与 `error` 均为 `None` | `{"code": 0, "msg": "ok", "data": null}` |

**降级不占用错误码** —— DeepSeek 不可用但模板可用时 `code` 仍为 `0`，通过 `data.degraded` 区分。

### `to_trip_card` 字段映射（对应 FR-4）

| TripCard 字段 | 来源 |
| --- | --- |
| `trip_id` | 调用方以参数传入 —— 落库后取自 `TripRecord.trip_id`，未落库时为空串 |
| `destination` | `TripPlan.destination` |
| `days` | `TripPlan.days` |
| `total_cost` | `TripPlan.total_cost` |
| `degraded` | `TripPlan.degraded` —— **必须透传** |
| `daily` | `TripPlan.daily` |
| `weather_report` | `TripPlan.weather_report`；为空时由 `daily[].weather` 汇总 |
| `cost_breakdown` | `TripPlan.cost_breakdown`；缺失时用 `days` / `total_cost` / `budget` 构造兜底结构 |
| `source` | `TripPlan.source`；为 `"unknown"` 或空时按 `degraded` 推断（`template` / `llm`） |

`TripPlan.budget` 属用户输入，不进卡片。

三个增强字段的兜底规则保证**卡片结构稳定**：即使旧数据（不含这些字段）从库中读回，
前端拿到的 `TripCard` 也始终有 `weather_report` 数组、`cost_breakdown` 对象与
`source` 字符串，无需额外判空分支。

### 关于裸 `dict` 的例外

CLAUDE.md 规定「禁止裸 dict 传递」。本模块有两处边界例外，均已限定范围：

1. `parse_chat_request(payload: dict)` —— 请求体在被 Pydantic 校验前天然是 `dict`。该 `dict` **不得**越过本方法边界，必须立即返回 `ChatRequest`。
2. `to_api_response(...) -> dict` —— 返回值是待序列化的响应体，处于系统出口边界。

除这两处外，`Bridge` 及其他任何模块都不得出现裸 `dict` 参数。

## 3. 外部依赖

| 依赖 | 用途 |
| --- | --- |
| `src/models` | `ChatRequest` / `TripCard` / `TripPlan` / `ApiResponse` / `CostResult` / `WeatherInfo` |
| `src/exceptions` | `AgentError` / `InvalidRequestError` |
| `src/scheduler` | `AgentResult` —— **仅用于类型标注**，不调用其任何方法 |

## 4. 异常处理

| 异常 | 抛出条件 | 处理方式 |
| --- | --- | --- |
| `InvalidRequestError` (1001) | `payload` 缺少 `message` 字段 | 本模块抛出，向上冒泡 |
| `InvalidRequestError` (1001) | `message` 非字符串 / 去空白后为空 / 超过 1000 字符 | 本模块抛出 |
| `InvalidRequestError` (1001) | `start_date` 不是合法 ISO 日期 | 本模块捕获 `ValidationError` 后抛出 |
| `InvalidRequestError` (1001) | `payload` 不是 `dict` | 本模块抛出 |

Pydantic 抛出的 `ValidationError` **必须**在此处捕获并转换为 `InvalidRequestError`，不得让其穿透到 `api/` 层 —— 否则错误码会落到兜底的 5000。

`to_api_response` / `to_trip_card` / `detect_action` **不抛业务异常**：`to_api_response` 是异常处理的终点，遇到任何 `error` 都须返回结构化响应体而非再次抛出；`detect_action` 对任何输入都能返回 `chat`，不存在失败路径。
