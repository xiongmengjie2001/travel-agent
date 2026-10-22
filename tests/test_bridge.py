"""Bridge 单测。

对应 SDD docs/module-sdd/bridge.md（含动作识别优先级表与 to_api_response 语义表）。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from src.bridge import (
    ACTION_CHAT,
    ACTION_DELETE_TRIP,
    ACTION_GENERAL_CHAT,
    ACTION_LIST_TRIPS,
    ACTION_PLAN_TRIP,
    ACTION_SEARCH_TRIPS,
    Bridge,
    _first_error,
)
from src.exceptions import AgentError, InvalidRequestError, TripNotFoundError
from src.models import CostResult, DailyPlan, Spot, TripPlan, WeatherInfo


def make_plan(*, degraded: bool = False) -> TripPlan:
    return TripPlan(
        destination="北京",
        days=1,
        budget=5000.0,
        total_cost=1200.0,
        daily=[DailyPlan(day_index=1, spots=[Spot(name="故宫")], estimated_cost=1200.0)],
        degraded=degraded,
    )


# ---------------------------------------------------------------- parse_chat_request


def test_parse_chat_request_returns_model() -> None:
    request = Bridge.parse_chat_request({"message": "  去北京玩3天  ", "session_id": "s1"})

    assert request.message == "去北京玩3天"
    assert request.session_id == "s1"


@pytest.mark.parametrize(
    "payload",
    [
        "不是字典",
        ["不是字典"],
        None,
    ],
)
def test_parse_chat_request_rejects_non_dict(payload: object) -> None:
    with pytest.raises(InvalidRequestError, match="JSON 对象"):
        Bridge.parse_chat_request(payload)  # type: ignore[arg-type]


def test_parse_chat_request_rejects_missing_message() -> None:
    with pytest.raises(InvalidRequestError, match="message"):
        Bridge.parse_chat_request({})


@pytest.mark.parametrize("message", ["", "   ", 123, None])
def test_parse_chat_request_rejects_bad_message(message: object) -> None:
    with pytest.raises(InvalidRequestError, match="校验失败"):
        Bridge.parse_chat_request({"message": message})


def test_parse_chat_request_rejects_overlong_message() -> None:
    with pytest.raises(InvalidRequestError, match="校验失败"):
        Bridge.parse_chat_request({"message": "北" * 1001})


def test_parse_chat_request_parses_start_date() -> None:
    request = Bridge.parse_chat_request(
        {"message": "去北京玩3天", "start_date": "2026-10-25"}
    )

    assert request.start_date == date(2026, 10, 25)


def test_parse_chat_request_leaves_start_date_optional() -> None:
    assert Bridge.parse_chat_request({"message": "去北京玩3天"}).start_date is None


@pytest.mark.parametrize("start_date", ["不是日期", "2026-13-40", "2026/10/25/extra"])
def test_parse_chat_request_rejects_bad_start_date(start_date: object) -> None:
    with pytest.raises(InvalidRequestError, match="校验失败"):
        Bridge.parse_chat_request({"message": "去北京玩3天", "start_date": start_date})


# ---------------------------------------------------------------- to_api_response


def test_to_api_response_wraps_pydantic_data() -> None:
    body = Bridge.to_api_response(data=Bridge.to_trip_card(make_plan(), "tr_20261003_8f3a"))

    assert body["code"] == 0
    assert body["msg"] == "ok"
    assert body["data"]["trip_id"] == "tr_20261003_8f3a"


def test_to_api_response_serializes_model_lists() -> None:
    body = Bridge.to_api_response(data=[Bridge.to_trip_card(make_plan())])

    assert isinstance(body["data"], list)
    assert body["data"][0]["destination"] == "北京"


def test_to_api_response_keeps_code_zero_for_degraded_trip() -> None:
    body = Bridge.to_api_response(data=Bridge.to_trip_card(make_plan(degraded=True)))

    assert body["code"] == 0
    assert body["data"]["degraded"] is True


def test_to_api_response_without_data() -> None:
    assert Bridge.to_api_response() == {"code": 0, "msg": "ok", "data": None}


def test_to_api_response_maps_agent_error() -> None:
    body = Bridge.to_api_response(error=TripNotFoundError("行程不存在"))

    assert body == {"code": 3001, "msg": "行程不存在", "data": None}


def test_to_api_response_maps_unexpected_error_to_5000() -> None:
    body = Bridge.to_api_response(error=ValueError("底层炸了"))

    assert body["code"] == AgentError.code
    assert body["data"] is None


def test_to_api_response_never_raises_on_broken_serialization() -> None:
    class Exploding(BaseModel):
        def model_dump(self, **kwargs: object) -> dict:  # type: ignore[override]
            raise RuntimeError("序列化炸了")

    body = Bridge.to_api_response(data=Exploding())

    assert body["code"] == AgentError.code
    assert body["data"] is None


# ---------------------------------------------------------------- to_trip_card


def test_to_trip_card_maps_fields_and_passes_degraded_through() -> None:
    card = Bridge.to_trip_card(make_plan(degraded=True), "tr_20261003_8f3a")

    assert card.trip_id == "tr_20261003_8f3a"
    assert card.destination == "北京"
    assert card.days == 1
    assert card.total_cost == 1200.0
    assert card.degraded is True
    assert card.daily[0].spots[0].name == "故宫"


def test_to_trip_card_without_trip_id_leaves_it_empty() -> None:
    assert Bridge.to_trip_card(make_plan()).trip_id == ""


def test_to_trip_card_passes_enhanced_fields_through() -> None:
    weather = [WeatherInfo(date=date(2026, 10, 25), condition="晴", temp_high=26.0)]
    cost = CostResult(days=1, total=1200.0, within_budget=True)
    plan = TripPlan(
        destination="北京",
        days=1,
        budget=5000.0,
        total_cost=1200.0,
        daily=[DailyPlan(day_index=1, spots=[Spot(name="故宫")], estimated_cost=1200.0)],
        weather_report=weather,
        cost_breakdown=cost,
        source="llm",
    )

    card = Bridge.to_trip_card(plan, "tr_20261003_8f3a")

    assert card.weather_report == weather
    assert card.cost_breakdown == cost
    assert card.source == "llm"


def test_to_trip_card_falls_back_to_daily_weather_and_total_cost() -> None:
    weather = WeatherInfo(date=date(2026, 10, 25), condition="多云")
    plan = TripPlan(
        destination="北京",
        days=1,
        total_cost=600.0,
        daily=[
            DailyPlan(
                day_index=1,
                spots=[Spot(name="故宫")],
                estimated_cost=600.0,
                weather=weather,
            )
        ],
        degraded=True,
    )

    card = Bridge.to_trip_card(plan)

    assert card.weather_report == [weather]
    assert card.cost_breakdown is not None
    assert card.cost_breakdown.total == 600.0
    assert card.cost_breakdown.within_budget is None
    # source 缺失时按 degraded 推断
    assert card.source == "template"


def test_to_trip_card_infers_llm_source_for_non_degraded_plan() -> None:
    assert Bridge.to_trip_card(make_plan()).source == "llm"


def test_to_api_response_serializes_nested_containers() -> None:
    body = Bridge.to_api_response(
        data={"card": Bridge.to_trip_card(make_plan()), "tags": ("a", "b"), "n": 1}
    )

    assert body["data"]["card"]["destination"] == "北京"
    assert body["data"]["tags"] == ["a", "b"]
    assert body["data"]["n"] == 1


def test_first_error_falls_back_when_details_are_empty() -> None:
    empty = ValidationError.from_exception_data("ChatRequest", [])

    assert _first_error(empty) == "格式不正确"


# ---------------------------------------------------------------- detect_action


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("删除北京行程", ACTION_DELETE_TRIP),
        ("把我的北京行程去掉", ACTION_DELETE_TRIP),
        ("删掉它", ACTION_DELETE_TRIP),
        ("查看我的行程", ACTION_LIST_TRIPS),
        ("给我看看历史行程", ACTION_LIST_TRIPS),
        ("搜索北京", ACTION_SEARCH_TRIPS),
        ("找一下上海的行程", ACTION_SEARCH_TRIPS),
        ("帮我规划去北京玩3天", ACTION_PLAN_TRIP),
        ("北京3天", ACTION_PLAN_TRIP),
        ("去杭州旅游", ACTION_PLAN_TRIP),
        ("你好", ACTION_CHAT),
        ("谢谢", ACTION_CHAT),
        ("讲个笑话", ACTION_CHAT),
        ("今天天气怎么样", ACTION_CHAT),
        ("", ACTION_CHAT),
    ],
)
def test_detect_action(text: str, action: str) -> None:
    assert Bridge.detect_action(text) == action


def test_general_chat_shares_the_chat_action_value() -> None:
    """一般对话是 chat 分支的语义别名，前端分派表无需改动。"""
    assert ACTION_GENERAL_CHAT == ACTION_CHAT


def test_detect_action_delete_wins_over_plan_keywords() -> None:
    # 「删除北京行程」同时含目的地与删除语义，必须判为删除（优先级 1 > 4）
    assert Bridge.detect_action("删除北京行程") == ACTION_DELETE_TRIP
