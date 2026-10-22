# Scheduler SDD

> 文件：`src/scheduler.py`

## 0. v3.0 变更（2026-10-21）：全链路异步

- `process_input`、`_execute_action` 与全部 `_handle_*` 改为 `async def`；
- `_handle_plan_trip` 中 `await reasoning.generate_itinerary(intent)`；
- 路由层 `POST /api/chat` 相应改为 `async def` 并 `await scheduler.process_input()`；
- `MemoryStore` 仍为同步实现（SQLite 本地短操作），本层直接调用，不再包线程池。

## 0.1 v4.0 变更（2026-10-21）：出发日期、目的地校验与一般对话

- `process_input(text, start_date=None)` / `_handle_plan_trip(text, start_date=None)`
  透传前端显式传入的出发日期（见 [date-support.md](./date-support.md)）；
- `plan_trip` 分支捕获 `DestinationInvalidError`，转为一般对话引导文案，
  **不生成、不落库**（见 [destination-validation.md](./destination-validation.md)）；
- 新增一般对话处理器 `_handle_chat()`：仅问候 / 闲聊的输入返回能力引导语，
  不触达 ReasoningEngine 与 MemoryStore；
- 新增动作别名 `ACTION_GENERAL_CHAT = ACTION_CHAT`，语义上区分「一般对话」
  与「兜底 chat」，动作值不变，前端分派表无需改动。

## 1. 功能描述

接收用户输入文本，识别动作并分派到对应处理器，编排 ReasoningEngine 与 MemoryStore 完成行程的生成、查询与删除。本模块只做编排与路由，不实现解析、生成、持久化等具体业务逻辑。

## 2. 接口签名

```python
class Scheduler:
    def __init__(self, reasoning: ReasoningEngine, memory: MemoryStore) -> None: ...
    async def process_input(self, text: str, start_date: date | None = None) -> AgentResult: ...
    async def _execute_action(
        self, action: str, text: str, start_date: date | None = None
    ) -> AgentResult: ...
    async def _handle_plan_trip(
        self, text: str, start_date: date | None = None
    ) -> AgentResult: ...
    async def _handle_list_trips(self, text: str) -> AgentResult: ...
    async def _handle_delete_trip(self, text: str) -> AgentResult: ...
    async def _handle_search_trips(self, text: str) -> AgentResult: ...
    async def _handle_chat(self, text: str) -> AgentResult: ...
```

| 方法 | 签名 | 可见性 | 含义 |
| --- | --- | --- | --- |
| `__init__` | `(reasoning: ReasoningEngine, memory: MemoryStore) -> None` | public | 注入下游依赖，不在内部自行实例化 |
| `process_input` | `async (text: str, start_date: date \| None = None) -> AgentResult` | public | 对话唯一入口：识别动作 → 分派 → 返回结果 |
| `_execute_action` | `async (action: str, text: str, start_date: date \| None = None) -> AgentResult` | private | 按 `action` 路由到对应 `_handle_*` |
| `_handle_plan_trip` | `async (text: str, start_date: date \| None = None) -> AgentResult` | private | `parse_intent(text, start_date)` → 校验目的地 → `await generate_itinerary` → `save_trip` |
| `_handle_list_trips` | `async (text: str) -> AgentResult` | private | `memory.list_trips()` |
| `_handle_delete_trip` | `async (text: str) -> AgentResult` | private | 从文本提取 `trip_id` → `memory.delete_trip()` |
| `_handle_search_trips` | `async (text: str) -> AgentResult` | private | 提取关键词 → `memory.search_trips()` |
| `_handle_chat` | `async (text: str) -> AgentResult` | private | 一般对话：返回能力引导语，不触达下游 |

### 返回值与动作常量

```python
ACTION_PLAN_TRIP = "plan_trip"
ACTION_LIST_TRIPS = "list_trips"
ACTION_SEARCH_TRIPS = "search_trips"
ACTION_DELETE_TRIP = "delete_trip"
ACTION_CHAT = "chat"
# 一般对话与兜底 chat 共用动作值，仅语义区分
ACTION_GENERAL_CHAT = ACTION_CHAT


class AgentResult(BaseModel):
    action: str
    message: str = ""
    trip: TripCard | None = None          # plan_trip
    trips: list[TripCard] = []            # list_trips / search_trips
```

`action` 取值与 [Bridge.detect_action()](./bridge.md) 的输出一一对应。`trip` 与 `trips` 至多一个非空。

### 动作分派表

| action | 调用链 |
| --- | --- |
| `plan_trip` | `reasoning.parse_intent(text, start_date)` → `await reasoning.generate_itinerary(intent)` → `memory.save_trip()` |
| `list_trips` | `memory.list_trips()` |
| `search_trips` | `memory.search_trips(keyword)` |
| `delete_trip` | `memory.delete_trip(trip_id)` |
| `chat` | `_handle_chat()`：不触碰下游，返回固定引导语 |

`plan_trip` 一律**先保存再返回** —— PRD AC-2 要求生成的行程自动入库。唯一例外是
目的地校验失败：此时转为 `chat` 分支的引导文案，不生成、不落库。

`start_date` 为 `None` 时由 `ReasoningEngine` 从文本解析或默认明天；显式传入时优先。

## 3. 外部依赖

| 依赖 | 用途 |
| --- | --- |
| `ReasoningEngine` | `parse_intent()` / `generate_itinerary()`，构造函数注入 |
| `MemoryStore` | `save_trip()` / `list_trips()` / `search_trips()` / `delete_trip()`，构造函数注入 |
| `Bridge.detect_action()` | 动作识别，模块级 import 调用 |
| SQLite `trips` 表 | 经 `MemoryStore` 间接触达，本模块不直接访问 |

> `__init__` 未接收 Bridge 实例，而 `process_input` 需要动作识别 —— 本 SDD 按「模块级 import `Bridge.detect_action()`」编写。若需保持可测试性，应改为构造函数注入第三个参数，这需要同时调整本签名。

## 4. 异常处理

| 异常 | 抛出条件 | 处理方式 |
| --- | --- | --- |
| `TripNotFoundError` (3001) | `delete_trip` 的 `trip_id` 在库中不存在 | 向上冒泡至 `api/` 层 |
| `IntentUnrecognizedError` (1002) | `parse_intent` 无法识别目的地 | 向上冒泡 |
| `DestinationInvalidError` (1003) | 目的地未通过有效性校验 | **本层捕获**，转为一般对话引导文案，不生成、不落库 |
| `AgentError` 各子类 | 由 ReasoningEngine / MemoryStore 抛出 | **原样冒泡，不捕获、不包装、不吞** |
| `InvalidRequestError` (1001) | 文本中无法提取出删除所需的 `trip_id` | 本模块直接抛出 |

**原则**：本模块是编排层，捕获下游异常会丢失原始错误码。唯二自行处理异常的场景是
「用户要求删除但文本中无有效 `trip_id`」与「目的地无效需转为引导文案」。

无法识别为任何动作的输入**不抛异常**，一律落到 `chat` 分支返回引导语。
