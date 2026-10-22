"""Scheduler 单测 —— OODA 循环的端到端编排。

对应 SDD docs/module-sdd/scheduler.md（动作分派表）。

依赖全部使用真实实现（ToolCenter 为 Mock 数据源，ReasoningEngine 无
API Key 走模板降级），因此**不发起任何网络请求**。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from src.exceptions import IntentUnrecognizedError, InvalidRequestError, TripNotFoundError
from src.mcp_client import ENV_AMAP_MCP_KEY, ENV_AMAP_MCP_URL
from src.memory import MemoryStore
from src.reasoning import ReasoningEngine
from src.scheduler import (
    ACTION_CHAT,
    ACTION_DELETE_TRIP,
    ACTION_GENERAL_CHAT,
    ACTION_LIST_TRIPS,
    ACTION_PLAN_TRIP,
    ACTION_SEARCH_TRIPS,
    Scheduler,
)
from src.tools import ToolCenter

TRIP_ID_RE = re.compile(r"^tr_\d{8}_[0-9a-f]{4}$")
PLAN_INPUT = "帮我规划去北京玩3天，预算5000元"


@pytest.fixture
def memory(tmp_path) -> MemoryStore:  # type: ignore[no-untyped-def]
    return MemoryStore(tmp_path / "trips.db")


@pytest.fixture(autouse=True)
def _without_mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉 MCP 配置，让目的地校验稳定走 Mock 白名单。"""
    monkeypatch.delenv(ENV_AMAP_MCP_URL, raising=False)
    monkeypatch.delenv(ENV_AMAP_MCP_KEY, raising=False)


@pytest.fixture
def scheduler(memory: MemoryStore) -> Scheduler:
    return Scheduler(ReasoningEngine(ToolCenter(), api_key=""), memory)


# ---------------------------------------------------------------- plan_trip


async def test_plan_trip_returns_saved_card(scheduler: Scheduler, memory: MemoryStore) -> None:
    result = await scheduler.process_input(PLAN_INPUT)

    assert result.action == ACTION_PLAN_TRIP
    assert result.trip is not None
    assert TRIP_ID_RE.match(result.trip.trip_id)
    assert result.trip.destination == "北京"
    assert result.trips == []
    assert "北京" in result.message


async def test_plan_trip_persists_before_returning(
    scheduler: Scheduler, memory: MemoryStore
) -> None:
    result = await scheduler.process_input(PLAN_INPUT)

    records = memory.list_trips()
    assert len(records) == 1
    assert records[0].trip_id == result.trip.trip_id  # type: ignore[union-attr]
    assert records[0].intent_raw == PLAN_INPUT


async def test_plan_trip_marks_degraded_in_message(scheduler: Scheduler) -> None:
    result = await scheduler.process_input(PLAN_INPUT)

    assert result.trip is not None
    assert result.trip.degraded is True
    assert "模板方案" in result.message


async def test_plan_trip_without_destination_raises(scheduler: Scheduler) -> None:
    # 「去」触发规划分支，但无城市可识别
    with pytest.raises(IntentUnrecognizedError):
        await scheduler.process_input("去玩3天")


async def test_plan_trip_passes_start_date_through(scheduler: Scheduler) -> None:
    start = date.today() + timedelta(days=4)

    result = await scheduler.process_input(PLAN_INPUT, start)

    assert result.trip is not None
    assert [day.date for day in result.trip.daily] == [
        start,
        start + timedelta(days=1),
        start + timedelta(days=2),
    ]
    assert [item.date for item in result.trip.weather_report] == [
        start,
        start + timedelta(days=1),
        start + timedelta(days=2),
    ]


async def test_plan_trip_invalid_destination_becomes_chat(
    scheduler: Scheduler, memory: MemoryStore
) -> None:
    # 西安不在 Mock 白名单内，且未配置 MCP → 目的地校验失败
    result = await scheduler.process_input("帮我规划去西安玩3天")

    assert result.action == ACTION_GENERAL_CHAT
    assert result.trip is None
    assert result.trips == []
    assert "西安" in result.message
    assert memory.list_trips() == []


# ---------------------------------------------------------------- list_trips


async def test_list_trips_when_empty(scheduler: Scheduler) -> None:
    result = await scheduler.process_input("查看我的行程")

    assert result.action == ACTION_LIST_TRIPS
    assert result.trips == []
    assert result.trip is None
    assert "暂无" in result.message


async def test_list_trips_after_planning(scheduler: Scheduler) -> None:
    await scheduler.process_input(PLAN_INPUT)

    result = await scheduler.process_input("我的历史行程")
    assert result.action == ACTION_LIST_TRIPS
    assert len(result.trips) == 1
    assert result.trips[0].destination == "北京"
    assert TRIP_ID_RE.match(result.trips[0].trip_id)


# ---------------------------------------------------------------- search_trips


async def test_search_trips_hits_destination(scheduler: Scheduler) -> None:
    await scheduler.process_input(PLAN_INPUT)

    result = await scheduler.process_input("搜索北京")
    assert result.action == ACTION_SEARCH_TRIPS
    assert len(result.trips) == 1


async def test_search_trips_without_match(scheduler: Scheduler) -> None:
    await scheduler.process_input(PLAN_INPUT)

    result = await scheduler.process_input("搜索三亚")
    assert result.trips == []


async def test_search_trips_without_keyword_raises(scheduler: Scheduler) -> None:
    with pytest.raises(InvalidRequestError, match="关键词"):
        await scheduler.process_input("搜索")


# ---------------------------------------------------------------- delete_trip


async def test_delete_trip_removes_record(
    scheduler: Scheduler, memory: MemoryStore
) -> None:
    result = await scheduler.process_input(PLAN_INPUT)
    trip_id = result.trip.trip_id  # type: ignore[union-attr]

    result = await scheduler.process_input(f"删除行程 {trip_id}")
    assert result.action == ACTION_DELETE_TRIP
    assert trip_id in result.message
    assert memory.list_trips() == []


async def test_delete_trip_without_id_raises(scheduler: Scheduler) -> None:
    with pytest.raises(InvalidRequestError, match="行程 ID"):
        await scheduler.process_input("删除北京行程")


async def test_delete_missing_trip_raises(scheduler: Scheduler) -> None:
    with pytest.raises(TripNotFoundError):
        await scheduler.process_input("删除行程 tr_20261003_ffff")


# ---------------------------------------------------------------- chat


@pytest.mark.parametrize("text", ["你好", "今天天气怎么样", ""])
async def test_chat_branch_returns_guide(scheduler: Scheduler, text: str) -> None:
    result = await scheduler.process_input(text)

    assert result.action == ACTION_CHAT
    assert result.trip is None
    assert result.trips == []
    assert result.message


async def test_chat_branch_does_not_touch_downstream(
    scheduler: Scheduler, memory: MemoryStore
) -> None:
    await scheduler.process_input("你好")

    assert memory.list_trips() == []


def test_general_chat_alias_matches_chat_action() -> None:
    assert ACTION_GENERAL_CHAT == ACTION_CHAT
