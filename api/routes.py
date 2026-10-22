"""FastAPI 端点定义与依赖装配。

CLAUDE.md 规定的职责边界：本层只做**请求校验 / 调用 `src/` / 异常转换**三件事，
不含任何业务逻辑；依赖方向恒为 `api/ → src/`。

分工：

- 业务编排交给 `Scheduler`（对话）与 `MemoryStore`（行程查询 / 删除），
  经 `Depends` 注入，本层**不自行实例化**；两者均为进程内单例（`lru_cache`）；
- 响应统一为 `ApiResponse[T]`（docs/schemas/api-response.md，AC-3）；
- 异常不在本层 try/except，一律冒泡到 `main.py` 注册的 handler 统一转换 ——
  错误码表见 docs/schemas/api-response.md §3，本层不得自造错误码。

⚠️ 已决分歧（bridge.md §2/§4 与「用 Pydantic 模型接收请求体」二选一）：
本层**采用 bridge.md 的方案** —— `/api/chat` 以 `dict` 接收请求体，再交给
`Bridge.parse_chat_request()` 校验并转换为 `ChatRequest`。代价是 OpenAPI 里该
端点的请求体退化为自由对象（字段规格见 docs/schemas/chat_request.json）；收益是
1001 的转换职责与 bridge.md §4 完全一致，且不会出现「框架先拒、Bridge 永远不会
被调用」的死代码。非 JSON 请求体仍由 `main.py` 的 `RequestValidationError`
handler 兜底为 1001。

⚠️ 未覆盖的端点：`GET /api/trips/search?keyword=` 与 `GET /api/health`
（见 docs/system-design.md 接口表）本次未实现 —— 检索能力目前只能经
`POST /api/chat` 的 search_trips 动作触达。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from fastapi import APIRouter, Body, Depends, Path, Query

from src.bridge import Bridge
from src.memory import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT, MIN_LIST_LIMIT, MemoryStore
from src.models import ApiResponse, DeleteTripResult, TripCard, TripRecord
from src.reasoning import ReasoningEngine
from src.scheduler import AgentResult, Scheduler
from src.tools import ToolCenter

ENV_MEMORY_DB_PATH = "MEMORY_DB_PATH"
# 与 .env.example 的默认值保持一致；实际路径一律由环境变量注入。
DEFAULT_MEMORY_DB_PATH = "./data/travel_agent.db"

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["travel-agent2"])


@lru_cache(maxsize=1)
def get_memory_store() -> MemoryStore:
    """FastAPI 依赖：提供进程内唯一的 `MemoryStore` 实例。

    库路径取自环境变量 `MEMORY_DB_PATH`，**不得硬编码**（CLAUDE.md 禁止事项）；
    未配置时回退 `DEFAULT_MEMORY_DB_PATH`。构造 `MemoryStore` 本身即完成建表
    （其 `__init__` 内执行建表脚本），故这里同时是「SQLite 表初始化」的入口。

    `lru_cache(maxsize=1)` 保证单例 —— 每请求新建实例会反复建立连接，并使
    `MemoryStore` 的模块级写锁形同虚设。

    Raises:
        MemoryStoreError: 库文件无法打开或建表失败（启动阶段即暴露，见 main.py）。
    """
    db_path = os.getenv(ENV_MEMORY_DB_PATH, DEFAULT_MEMORY_DB_PATH)
    store = MemoryStore(db_path)
    LOGGER.info("记忆库已就绪: %s", db_path)
    return store


@lru_cache(maxsize=1)
def get_scheduler() -> Scheduler:
    """FastAPI 依赖：提供进程内唯一的 `Scheduler` 实例。

    内部复用 `get_memory_store()` 的**同一个** `MemoryStore` —— 若两者返回不同
    实例，对话写入与 RESTful 读取会走不同连接，甚至指向不同文件。

    `ReasoningEngine` 的 API Key 由环境变量 `DEEPSEEK_API_KEY` 决定，缺省即进入
    纯模板模式（对应 AC-5），不会发起网络请求。
    """
    return Scheduler(ReasoningEngine(ToolCenter()), get_memory_store())


@router.post("/chat", response_model=ApiResponse[AgentResult])
async def chat(
    payload: dict = Body(
        ...,
        description="对话请求体；字段规格见 docs/schemas/chat_request.json",
    ),
    scheduler: Scheduler = Depends(get_scheduler),
) -> ApiResponse[AgentResult]:
    """对话接口（FR-1 / FR-4）—— 全应用唯一的自然语言入口。

    `payload` 交给 `Bridge.parse_chat_request()` 校验为 `ChatRequest`，其
    `message` 再交给 `Scheduler.process_input()`；返回的 `AgentResult` 中
    `action` 标明实际命中的分支（plan_trip / list_trips / search_trips /
    delete_trip / chat），`trip` 与 `trips` 至多一个非空。

    响应体经 `Bridge.to_api_response()` 组装（该 dict 是 CLAUDE.md 已确认的
    出口边界例外），再由 `response_model` 校验回 `ApiResponse[AgentResult]`。

    Args:
        payload: 前端对话请求体。`session_id` 本阶段仅透传 —— FR-4 的多轮会话
            尚未设计，不参与任何分支。

    Raises（冒泡至 main.py 的 handler 后转换为 ApiResponse）:
        InvalidRequestError: 1001 / HTTP 400。
        IntentUnrecognizedError: 1002 / HTTP 200。
        LLMUnavailableError: 2001 / HTTP 200 —— 仅「LLM 失败且模板也不可用」。
        TripNotFoundError: 3001 / HTTP 200。
        MemoryStoreError: 3002 / HTTP 200。

    注：`degraded=True` 的行程**不抛异常**，仍为 code 0（AC-5）。
    """
    request = Bridge.parse_chat_request(payload)
    result = await scheduler.process_input(request.message, request.start_date)
    return ApiResponse[AgentResult].model_validate(Bridge.to_api_response(data=result))


@router.get("/trips", response_model=ApiResponse[list[TripCard]])
def list_trips(
    limit: int = Query(
        default=DEFAULT_LIST_LIMIT,
        ge=MIN_LIST_LIMIT,
        le=MAX_LIST_LIMIT,
        description="返回条数上限",
    ),
    memory: MemoryStore = Depends(get_memory_store),
) -> ApiResponse[list[TripCard]]:
    """获取历史行程（FR-2，对应 AC-2），按 `created_at` 倒序。

    直连 `MemoryStore.list_trips()`，**不经过 Scheduler** —— 这是 RESTful 查询，
    没有需要 `Bridge.detect_action()` 识别的自然语言输入。

    `list_trips()` 返回 `list[TripRecord]`，而前端契约是 `TripCard`：两者经
    `Bridge.to_trip_card(record.itinerary, record.trip_id)` 转换 —— `trip_id`
    由落库时生成，只有 `TripRecord` 才有，故必须从记录侧回填。

    Args:
        limit: 条数上限。边界同时由 Query 与 `MemoryStore.list_trips()` 校验：
            本层先拒（1001 / HTTP 400），下层保留是为直接调用方的防御，两处结果一致。

    Raises:
        InvalidRequestError: 1001 / HTTP 400 —— limit 越界。
        MemoryStoreError: 3002 / HTTP 200。
    """
    records = memory.list_trips(limit=limit)
    return ApiResponse.ok(
        [Bridge.to_trip_card(record.itinerary, record.trip_id) for record in records]
    )


@router.get("/trips/{trip_id}", response_model=ApiResponse[TripRecord])
def get_trip(
    trip_id: str = Path(min_length=1, description="目标行程 ID"),
    memory: MemoryStore = Depends(get_memory_store),
) -> ApiResponse[TripRecord]:
    """获取单条完整行程详情（含增强字段）。

    与列表接口的区别：列表返回裁剪后的 `TripCard`，本接口返回 `TripRecord`，
    额外带 `created_at`、`intent_raw` 与完整的 `itinerary`（其中包含
    `weather_report` / `cost_breakdown` / `source` 三个增强字段）。

    Raises:
        InvalidRequestError: 1001 / HTTP 400 —— trip_id 为空白。
        TripNotFoundError: 3001 / HTTP 200 —— trip_id 不存在。
        MemoryStoreError: 3002 / HTTP 200。
    """
    return ApiResponse.ok(memory.get_trip(trip_id))


@router.delete("/trips/{trip_id}", response_model=ApiResponse[DeleteTripResult])
def delete_trip(
    trip_id: str = Path(min_length=1, description="目标行程 ID"),
    memory: MemoryStore = Depends(get_memory_store),
) -> ApiResponse[DeleteTripResult]:
    """删除指定行程（FR-2，对应 AC-2）。

    直连 `MemoryStore.delete_trip()`，**不经过 Scheduler** —— 对话语料中的删除由
    `Scheduler._handle_delete_trip()` 从文本里提取 trip_id，而本端点由前端直接
    携带 ID，无需意图识别。

    成功时 `data` 返回 `{trip_id, deleted: true}`，给出显式的成功标识，调用方
    不必只靠 `code` / `msg` 推断删除结果。

    Args:
        trip_id: 目标行程 ID，形如 `tr_20261003_8f3a`。

    Raises:
        TripNotFoundError: 3001 / HTTP 200 —— trip_id 不存在。错误码归 3xxx
            （数据层）而非 500：这是预期的业务分支，不是服务故障。注意
            `MemoryStore.delete_trip()` 对不存在的 ID **抛异常而非返回 False**。
        MemoryStoreError: 3002 / HTTP 200。
    """
    memory.delete_trip(trip_id)
    return ApiResponse.ok(DeleteTripResult(trip_id=trip_id, deleted=True))
