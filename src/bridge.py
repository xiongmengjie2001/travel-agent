"""Bridge：前后端数据格式适配层 + 用户输入动作识别。

负责把原始请求体转为内部模型、把内部结果转为统一响应格式与前端卡片格式，
同时承担动作识别（detect_action）。本模块不产生业务数据，不访问数据库。

⚠️ `to_trip_card` 相对 SDD 的签名扩展：增加可选参数 `trip_id`。
SDD §2 的 `to_trip_card(plan: TripPlan)` 无处取得 `trip_id` —— 它由
MemoryStore 落库时才生成，而 `TripPlan` 不含该字段；`TripCard.trip_id`
却是前端删除 / 修改行程的唯一句柄（AC-2）。只传 `plan` 时 `trip_id` 为空串。

调用方取卡片的两条路径：

- `Scheduler._handle_plan_trip` / `_handle_list_trips` /
  `_handle_search_trips`：`to_trip_card(record.itinerary, record.trip_id)`
- 尚无 ID 的临时展示：`to_trip_card(plan)`

规格见 docs/module-sdd/bridge.md。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.exceptions import AgentError, InvalidRequestError
from src.models import (
    ApiResponse,
    ChatRequest,
    CostResult,
    TripCard,
    TripPlan,
    WeatherInfo,
)

if TYPE_CHECKING:
    # 仅用于类型标注，不在运行时导入 —— scheduler 反向依赖本模块的
    # detect_action()，模块级互引会形成循环 import。
    from src.scheduler import AgentResult

ACTION_PLAN_TRIP = "plan_trip"
ACTION_LIST_TRIPS = "list_trips"
ACTION_SEARCH_TRIPS = "search_trips"
ACTION_DELETE_TRIP = "delete_trip"
ACTION_CHAT = "chat"
# 「一般对话」与既有 chat 共用同一个动作值：前端与 Scheduler 分派表都按
# "chat" 识别，新增别名只为在语义上区分「仅问候 / 闲聊」这一分支。
ACTION_GENERAL_CHAT = ACTION_CHAT

# 动作识别关键词表，按 detect_action 的优先级自上而下排列。
DELETE_KEYWORDS = ("删除", "删掉", "移除", "去掉")
LIST_KEYWORDS = ("查看", "我的行程", "历史", "列表")
SEARCH_KEYWORDS = ("搜索", "查找", "检索", "找一下", "找找")
PLAN_KEYWORDS = ("规划", "行程", "旅行", "旅游", "度假", "游玩", "去", "玩", "游")

# 「北京 3 天」这类只报目的地 + 天数的说法也应判为规划。
PLAN_DAYS_PATTERN = re.compile(r"\d+\s*(?:天|日)")


class Bridge:
    """无状态适配层，四个方法均为静态方法。"""

    @staticmethod
    def parse_chat_request(payload: dict) -> ChatRequest:
        """原始请求体 → 内部模型。

        Args:
            payload: 未经校验的请求体。这是 CLAUDE.md 已确认的裸 dict 例外
                之一；该 dict **不得**越过本方法边界。

        Returns:
            ChatRequest。

        Raises:
            InvalidRequestError: payload 不是 dict、缺少 message、
                message 非字符串 / 去空白后为空 / 超过 1000 字符。
                Pydantic 的 ValidationError 必须在此转换为 InvalidRequestError，
                否则错误码会落到兜底的 5000。
        """
        if not isinstance(payload, dict):
            raise InvalidRequestError("请求体必须是 JSON 对象")
        if "message" not in payload:
            raise InvalidRequestError("请求体缺少 message 字段")
        try:
            return ChatRequest.model_validate(payload)
        except ValidationError as exc:
            raise InvalidRequestError(f"请求参数校验失败: {_first_error(exc)}") from exc

    @staticmethod
    def to_api_response(data: Any = None, error: Exception | None = None) -> dict:
        """组装统一响应体（AC-3：`{code, msg, data}`）。

        **永不抛异常** —— 本方法是异常处理的终点，遇到任何 error 都须返回
        结构化响应体而非再次抛出。

        Args:
            data: 业务数据；`degraded=True` 的行程仍返回 code 0（AC-5）。
            error: 异常实例；AgentError 子类按其 code，其余归为 5000。
        """
        try:
            if error is not None:
                if isinstance(error, AgentError):
                    return ApiResponse.fail(code=error.code, msg=error.msg).model_dump()
                return ApiResponse.fail(code=AgentError.code, msg="内部错误").model_dump()
            if data is None:
                return ApiResponse(code=0, msg="ok", data=None).model_dump()
            return ApiResponse(
                code=0, msg="ok", data=_to_jsonable(data)
            ).model_dump(mode="json")
        except Exception:  # 序列化兜底：宁可回 5000，也不让本方法抛出
            return ApiResponse.fail(code=AgentError.code, msg="内部错误").model_dump()

    @staticmethod
    def to_trip_card(plan: TripPlan, trip_id: str = "") -> TripCard:
        """裁剪出前端卡片所需字段（对应 FR-4）。

        `degraded` 必须透传 —— 前端据此展示降级提示（AC-5）。`budget` 属用户
        输入，不进卡片；天气随 `daily[].weather` 一并带出。

        三个增强字段（`weather_report` / `cost_breakdown` / `source`）直接
        透传，缺失时按逐日天气、总花费与 `degraded` 兜底，保证卡片结构稳定。

        Args:
            plan: 行程方案。
            trip_id: 落库后拿到的行程 ID，作为前端操作句柄。未落库时留空。
        """
        return TripCard(
            trip_id=trip_id,
            destination=plan.destination,
            days=plan.days,
            total_cost=plan.total_cost,
            degraded=plan.degraded,
            daily=plan.daily,
            weather_report=_weather_report(plan),
            cost_breakdown=_cost_breakdown(plan),
            source=_source(plan),
        )

    @staticmethod
    def detect_action(text: str) -> str:
        """识别动作，返回动作常量字符串。

        按「删除 > 查看 > 检索 > 规划」自上而下匹配，命中即停；
        均不匹配时返回 `ACTION_CHAT`。任何输入都能返回结果，无失败路径。
        """
        content = text or ""
        if _contains_any(content, DELETE_KEYWORDS):
            return ACTION_DELETE_TRIP
        if _contains_any(content, LIST_KEYWORDS):
            return ACTION_LIST_TRIPS
        if _contains_any(content, SEARCH_KEYWORDS):
            return ACTION_SEARCH_TRIPS
        if _contains_any(content, PLAN_KEYWORDS) or PLAN_DAYS_PATTERN.search(content):
            return ACTION_PLAN_TRIP
        return ACTION_CHAT


# ---------------------------------------------------------------- 模块级辅助


def _weather_report(plan: TripPlan) -> list[WeatherInfo]:
    """多日天气报告：优先用方案自带的，缺失时从逐日天气汇总。"""
    if plan.weather_report:
        return plan.weather_report
    return [day.weather for day in plan.daily if day.weather is not None]


def _cost_breakdown(plan: TripPlan) -> CostResult:
    """花费分项：缺失时用总花费与预算构造一个仅含总额的兜底结构。"""
    if plan.cost_breakdown is not None:
        return plan.cost_breakdown
    return CostResult(
        days=plan.days,
        total=plan.total_cost,
        within_budget=None if plan.budget is None else plan.total_cost <= plan.budget,
    )


def _source(plan: TripPlan) -> str:
    """来源标识：缺失或 unknown 时按 degraded 推断。"""
    if plan.source and plan.source != "unknown":
        return plan.source
    return "template" if plan.degraded else "llm"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """文本是否命中任一关键词。"""
    return any(keyword in text for keyword in keywords)


def _to_jsonable(value: Any) -> Any:
    """把 Pydantic 模型 / 容器递归转为可 JSON 序列化的结构。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _first_error(exc: ValidationError) -> str:
    """取 Pydantic 首个错误的可读描述，避免把整份 errors() 回传给前端。"""
    errors = exc.errors()
    if not errors:
        return "格式不正确"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "message"
    return f"{location}: {first.get('msg', '格式不正确')}"
