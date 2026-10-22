"""Scheduler：OODA 决策循环的编排层。

接收用户输入文本，识别动作并分派到对应处理器，编排 ReasoningEngine 与
MemoryStore 完成行程的生成、查询与删除。本模块**只做编排与路由**，不实现
解析、生成、持久化等具体业务逻辑。

动作识别由 Bridge.detect_action() 承担，本模块不重复实现。

v3.0 起入口 `process_input()` 与全部动作处理器为异步，`plan_trip` 分支 await
ReasoningEngine 的异步生成；`MemoryStore` 仍为同步实现，直接调用。

规格见 docs/module-sdd/scheduler.md。
"""

from __future__ import annotations

import logging
import re
from datetime import date

from pydantic import BaseModel, Field

from src.bridge import Bridge
from src.exceptions import DestinationInvalidError, InvalidRequestError
from src.memory import MemoryStore
from src.models import TripCard, TripRecord
from src.reasoning import ReasoningEngine

ACTION_PLAN_TRIP = "plan_trip"
ACTION_LIST_TRIPS = "list_trips"
ACTION_SEARCH_TRIPS = "search_trips"
ACTION_DELETE_TRIP = "delete_trip"
ACTION_CHAT = "chat"
# 「一般对话」分支与 chat 共用动作值，语义上专指仅问候 / 闲聊的输入。
ACTION_GENERAL_CHAT = ACTION_CHAT

# trip_id 形如 tr_20261003_8f3a（见 src/memory.py）。
TRIP_ID_PATTERN = re.compile(r"tr_\d{8}_[0-9a-fA-F]{4}")

# 从检索语句里剥掉的动词与填充词，剩下的即关键词。
SEARCH_FILLERS = (
    "搜索",
    "查找",
    "检索",
    "找一下",
    "找找",
    "帮我",
    "请",
    "一下",
    "的行程",
    "行程",
    "记录",
    "历史",
)

CHAT_GUIDE = (
    "我可以帮你规划旅行行程、查看历史行程、搜索或删除行程。"
    "试试这样说：帮我规划去北京玩 3 天，预算 5000 元。"
)

INVALID_DESTINATION_GUIDE = (
    "没有找到「{destination}」这个目的地，可能是名称有误或暂不支持。"
    "换个城市试试？例如：帮我规划去北京玩 3 天，预算 5000 元。"
)

LOGGER = logging.getLogger(__name__)


class AgentResult(BaseModel):
    """Scheduler 单次处理的结果。

    由 Bridge.to_api_response() 消费。`trip` 与 `trips` 至多一个非空。

    ⚠️ 本模型由 Scheduler 产出、被 Bridge 仅用作类型标注 —— 按 CLAUDE.md
    的收录标准（被两个以上模块引用）它本应归入 `src/models/`，此处暂按
    scheduler SDD 的定义就地声明，待确认后再决定是否上移。
    """

    action: str = Field(description="动作常量，取值见本模块 ACTION_* 常量")
    message: str = ""
    trip: TripCard | None = Field(default=None, description="plan_trip 的结果")
    trips: list[TripCard] = Field(default_factory=list, description="list / search 的结果")


class Scheduler:
    """对话入口与动作分派。"""

    def __init__(self, reasoning: ReasoningEngine, memory: MemoryStore) -> None:
        """注入下游依赖，不在内部自行实例化。

        Args:
            reasoning: 意图解析与行程生成。
            memory: 行程持久化。
        """
        self._reasoning = reasoning
        self._memory = memory

    async def process_input(
        self, text: str, start_date: date | None = None
    ) -> AgentResult:
        """对话唯一入口：识别动作 → 分派 → 返回结果（异步）。

        Observe 记录原始输入，Orient 交给 Bridge 识别动作，Decide + Act 由
        `_execute_action` 分派到对应处理器。

        无法识别为任何动作的输入**不抛异常**，一律落到一般对话分支返回引导语。

        Args:
            text: 用户原始输入。
            start_date: 前端显式传入的出发日期；为 None 时由 ReasoningEngine
                从文本解析或默认明天。

        Raises:
            AgentError: 下游模块抛出的异常原样冒泡，本层不捕获、不包装。
                唯一例外是 `DestinationInvalidError` —— 它被转换为一般对话
                引导文案，不生成行程、不落库。
        """
        LOGGER.info("Observe: 收到输入 %r", text)
        action = Bridge.detect_action(text)
        LOGGER.info("Orient: 判定动作为 %s", action)
        return await self._execute_action(action, text, start_date)

    async def _execute_action(
        self, action: str, text: str, start_date: date | None = None
    ) -> AgentResult:
        """按 action 路由到对应的 _handle_* 处理器。"""
        if action == ACTION_PLAN_TRIP:
            return await self._handle_plan_trip(text, start_date)
        if action == ACTION_LIST_TRIPS:
            return await self._handle_list_trips(text)
        if action == ACTION_SEARCH_TRIPS:
            return await self._handle_search_trips(text)
        if action == ACTION_DELETE_TRIP:
            return await self._handle_delete_trip(text)
        return await self._handle_chat(text)

    async def _handle_chat(self, text: str) -> AgentResult:
        """一般对话处理器：仅问候 / 闲聊，返回能力引导，不触达下游。"""
        LOGGER.info("Act: 一般对话，返回引导语（输入 %r）", text)
        return AgentResult(action=ACTION_GENERAL_CHAT, message=CHAT_GUIDE)

    async def _handle_plan_trip(
        self, text: str, start_date: date | None = None
    ) -> AgentResult:
        """parse_intent → 校验目的地 → generate_itinerary → save_trip。

        一律**先保存再返回** —— AC-2 要求生成的行程自动入库。目的地无效时
        转为一般对话引导文案，不生成、不落库。

        Raises:
            IntentUnrecognizedError: 由 ReasoningEngine 抛出，原样冒泡。
            LLMUnavailableError: 由 ReasoningEngine 抛出，原样冒泡。
            MemoryStoreError: 落库失败，原样冒泡。
        """
        intent = self._reasoning.parse_intent(text, start_date)
        try:
            plan = await self._reasoning.generate_itinerary(intent)
        except DestinationInvalidError:
            LOGGER.info("Act: 目的地「%s」无效，转为对话引导", intent.destination)
            return AgentResult(
                action=ACTION_GENERAL_CHAT,
                message=INVALID_DESTINATION_GUIDE.format(destination=intent.destination),
            )
        record = self._memory.save_trip(plan, intent_raw=intent.raw_text)

        message = (
            f"已为你规划「{record.destination}」{record.days} 天行程，"
            f"预估总花费 {record.itinerary.total_cost:.0f} 元。"
        )
        if record.degraded:
            message += "（当前为模板方案，未接入大模型）"
        LOGGER.info("Act: 行程已入库 %s", record.trip_id)
        return AgentResult(action=ACTION_PLAN_TRIP, message=message, trip=_to_card(record))

    async def _handle_list_trips(self, text: str) -> AgentResult:
        """memory.list_trips()。"""
        records = self._memory.list_trips()
        trips = [_to_card(record) for record in records]
        message = f"共 {len(trips)} 条历史行程。" if trips else "暂无历史行程。"
        LOGGER.info("Act: 列出 %d 条行程", len(trips))
        return AgentResult(action=ACTION_LIST_TRIPS, message=message, trips=trips)

    async def _handle_delete_trip(self, text: str) -> AgentResult:
        """从文本提取 trip_id → memory.delete_trip()。

        Raises:
            InvalidRequestError: 文本中无法提取出有效的 trip_id。
            TripNotFoundError: 由 MemoryStore 抛出，原样冒泡。
        """
        trip_id = _extract_trip_id(text)
        if not trip_id:
            raise InvalidRequestError(
                "未能从输入中识别出行程 ID，请提供形如 tr_20261003_8f3a 的 ID"
            )
        self._memory.delete_trip(trip_id)
        LOGGER.info("Act: 已删除行程 %s", trip_id)
        return AgentResult(action=ACTION_DELETE_TRIP, message=f"已删除行程 {trip_id}。")

    async def _handle_search_trips(self, text: str) -> AgentResult:
        """从文本提取关键词 → memory.search_trips()。

        Raises:
            InvalidRequestError: 关键词被剥空（由 MemoryStore 抛出）。
            MemoryStoreError: 检索失败，原样冒泡。
        """
        keyword = _extract_keyword(text)
        records = self._memory.search_trips(keyword)
        trips = [_to_card(record) for record in records]
        message = f"找到 {len(trips)} 条与「{keyword}」相关的行程。"
        LOGGER.info("Act: 检索「%s」命中 %d 条", keyword, len(trips))
        return AgentResult(action=ACTION_SEARCH_TRIPS, message=message, trips=trips)


# ---------------------------------------------------------------- 模块级辅助


def _to_card(record: TripRecord) -> TripCard:
    """TripRecord → TripCard（带上落库后才有 trip_id）。

    注：`TripRecord` 与 `TripPlan` 的字段映射由 Bridge 负责，本函数只补
    `trip_id` 这一个落库期才存在的字段。
    """
    return Bridge.to_trip_card(record.itinerary, record.trip_id)


def _extract_trip_id(text: str) -> str | None:
    """从文本中提取 trip_id；未命中返回 None。"""
    match = TRIP_ID_PATTERN.search(text or "")
    return match.group(0) if match else None


def _extract_keyword(text: str) -> str:
    """剥掉检索动词与填充词，返回关键词。

    剥空时返回空串，交由 MemoryStore.search_trips 抛 `InvalidRequestError`
    —— 别名的校验规则只在一处维护。
    """
    cleaned = text or ""
    for filler in SEARCH_FILLERS:
        cleaned = cleaned.replace(filler, " ")
    return " ".join(cleaned.split())
