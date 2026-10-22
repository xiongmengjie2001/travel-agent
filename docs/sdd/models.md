# SDD：共享模型层（models）

> 对应系统设计 [第 3 节](../system-design.md)、契约 [docs/schemas/api-response.md](../schemas/api-response.md)
> 版本：v1.1
> 日期：2026-10-21
> 位置：`src/models/`

## 0. v1.1 变更（2026-10-21）

- `TripPlan` / `TripCard` 新增 `weather_report`（多日天气报告）、`cost_breakdown`
  （花费四段分项，`CostResult | None`）、`source`（`llm` / `template` / `unknown`）；
- `ChatRequest` / `Intent` 新增可选字段 `start_date: date | None`（出发日期）；
- 新增 `DeleteTripResult`（`trip_id` + `deleted`），作为删除接口的显式返回；
- 天气模型正式名称为 `WeatherInfo`（旧文档中的 `Weather` 为笔误）。

## 1. 功能描述

跨模块共享的 Pydantic 模型层，只定义类型契约，不承载业务逻辑。

**为什么需要独立一层**：`TripPlan` 由 ReasoningEngine 产出、被 MemoryStore 持久化、被 Bridge 转换；`TripRecord` 由 MemoryStore 产出、被 Bridge 消费。若把这些模型定义在任一业务模块内部，其他模块引用它就会产生**同级模块之间的横向依赖**，与系统设计第 3 节的单向依赖表冲突。抽出独立一层后，所有业务模块只依赖 models，而 models 不依赖任何业务模块。

**边界**：

- 只含 `BaseModel` 定义与纯校验逻辑（如去空白），不含 IO、不含业务规则
- 不 import 任何 `src/` 下的其他模块
- 不 import FastAPI 或任何 Web 框架

## 2. 模块内容

| 文件 | 模型 | 主要消费方 |
| --- | --- | --- |
| `itinerary.py` | `WeatherInfo`, `Spot`, `DailyPlan`, `TripPlan` | ReasoningEngine, MemoryStore, Bridge |
| `tool_results.py` | `Attraction`, `RouteResult`, `CostResult` | ToolCenter, ReasoningEngine, Bridge |
| `trip.py` | `TripRecord`, `TripCard`, `DeleteTripResult` | MemoryStore, Bridge, API |
| `chat.py` | `ChatRequest` | Bridge, API |
| `intent.py` | `Intent` | ReasoningEngine, Scheduler |
| `api_response.py` | `ApiResponse[T]` | Bridge, API |

> `intent.py` 与 `api_response.py` 是**模块间契约**（分别对应 Scheduler ↔ ReasoningEngine、
> API ↔ Bridge），与 `itinerary.py` / `trip.py` 的**领域模型**同层存放 —— 收录标准是
> 「被两个以上模块引用」，而非「是不是领域概念」。这条标准是保证同级模块之间不出现
> 横向 import 的前提。

### 关键约束

- `DailyPlan.spots` 的列表顺序即当日游览路线顺序（对应 AC-1 的「路线顺序」），消费方不得重排
- `TripPlan.degraded` / `TripCard.degraded` / `TripRecord.degraded` 三处同名同义，必须端到端一致传递（对应 AC-5）
- `weather_report` 与 `daily[].date` 使用**同一时间轴**，且首日即出发日期；天气获取失败时为空列表，不阻断行程
- `cost_breakdown.total` 必须等于 `TripPlan.total_cost` / `TripCard.total_cost`
- `TripCard` 是前端的唯一契约，前端不直接消费 `TripPlan`（对应 FR-4）
- `ChatRequest.message` 长度上限 1000 字符，超出即拒绝
- `start_date` 为 `date | None`：`None` 表示未指定，由 ReasoningEngine 按「文本日期 > 明天」补全（见 [date-support.md](../module-sdd/date-support.md)）

## 3. 外部依赖

| 依赖 | 用途 |
| --- | --- |
| `pydantic` v2 | 模型定义与序列化 |
| Python 标准库 `datetime` | 日期与时间戳类型 |

无数据库访问、无网络调用。

## 4. 异常处理

本层不定义也不抛出业务异常。

- 字段校验失败抛 Pydantic 原生 `ValidationError`，由 `api/` 层统一捕获并转换为 `InvalidRequestError`（code 1001）
- `ChatRequest.message` 的空白校验同样走 `ValidationError`，不自行抛 `AgentError`

> 异常体系（`AgentError` 及其子类）**不属于本层** —— 它是行为契约而非数据契约，位于 [src/exceptions.py](../../src/exceptions.py)。

## 5. 验收清单

- [ ] `from src.models import TripPlan, TripRecord, ChatRequest` 可直接导入
- [ ] `TripPlan(destination="北京", days=3)` 构造成功，`degraded` 默认为 `False`
- [ ] `TripPlan(destination="北京", days=0)` 抛 `ValidationError`
- [ ] `ChatRequest(message="   ")` 抛 `ValidationError`
- [ ] `ChatRequest(message="  北京3日游  ")` 的 `message` 被 normalize 为 `"北京3日游"`
- [ ] `ChatRequest(message="北京3日游", start_date="2026-10-25")` 的 `start_date` 被解析为 `date`；非法日期抛 `ValidationError`
- [ ] `TripPlan` / `TripCard` 的 `weather_report` / `cost_breakdown` / `source` 序列化往返一致，且缺省时分别为 `[]` / `None` / `"unknown"`
- [ ] `DeleteTripResult` 序列化为 `{"trip_id": "...", "deleted": true}`
- [ ] `TripRecord` 嵌套 `TripPlan` 可完整序列化 / 反序列化往返
- [ ] 本层任何文件均不 import `src/` 下其他模块
- [ ] `tests/test_models.py` 覆盖率 100%（核心模块，CLAUDE.md 要求）

## 6. 相关文件

- 异常体系（`AgentError` 及子类）位于 [src/exceptions.py](../../src/exceptions.py)，**不属于本层** —— 它是行为契约而非数据契约，因此与 `src/models/` 包并列存放。
