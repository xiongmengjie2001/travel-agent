"""api/routes.py 端点测试（T10）。

覆盖三个端点的成功路径、业务错误码与 HTTP 状态码，并核对响应体恒为
`{code, msg, data}` 三字段（docs/schemas/api-response.md）。

全部用例**不发起网络请求**：fixture 显式清空 `DEEPSEEK_API_KEY`，让
`ReasoningEngine` 走模板降级路径。

数据库指向 `tmp_path` —— 通过环境变量 `MEMORY_DB_PATH` 注入，顺带验证
路径确实来自环境变量而非硬编码（CLAUDE.md 禁止事项）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.routes import ENV_MEMORY_DB_PATH, get_memory_store, get_scheduler
from main import app
from src.bridge import (
    ACTION_CHAT,
    ACTION_DELETE_TRIP,
    ACTION_LIST_TRIPS,
    ACTION_PLAN_TRIP,
)
from src.exceptions import AgentError, InvalidRequestError, TripNotFoundError
from src.memory import MAX_LIST_LIMIT

TRIP_ID_RE = re.compile(r"^tr_\d{8}_[0-9a-f]{4}$")
CHAT_URL = "/api/chat"
TRIPS_URL = "/api/trips"
PLAN_MESSAGE = "帮我规划去北京玩3天"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """指向临时库的 TestClient。

    两个依赖访问器都带 `lru_cache`，实例在进程内跨用例存活，故进入与退出都必须
    清缓存 —— 否则第一个用例的库路径会被后续用例复用（`tmp_path` 每例不同），
    拿到的是已被清理的空库或指向不存在文件的实例。

    `with` 进入时触发 lifespan 启动事件（建表），故用例内不需要额外的初始化调用。

    `raise_server_exceptions=False`：Starlette 的 ServerErrorMiddleware 处理完
    `Exception` handler 后**总会重新抛出**，保持默认值会让「兜底 5000」的用例
    在测试里直接炸出原始异常，测不到 5000 响应。AgentError 走的是
    ExceptionMiddleware，不受该参数影响。
    """
    monkeypatch.setenv(ENV_MEMORY_DB_PATH, str(tmp_path / "api.db"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    get_memory_store.cache_clear()
    get_scheduler.cache_clear()

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    get_scheduler.cache_clear()
    get_memory_store.cache_clear()


def create_trip(client: TestClient, message: str = PLAN_MESSAGE) -> str:
    """经 `/api/chat` 建一条行程，返回其 trip_id。"""
    response = client.post(CHAT_URL, json={"message": message})
    assert response.status_code == 200
    trip_id = response.json()["data"]["trip"]["trip_id"]
    assert TRIP_ID_RE.match(trip_id)
    return trip_id


# ---------------------------------------------------------------- 装配与契约


def test_startup_event_creates_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "nested" / "api.db"
    monkeypatch.setenv(ENV_MEMORY_DB_PATH, str(db_path))
    get_memory_store.cache_clear()
    get_scheduler.cache_clear()

    with TestClient(app) as test_client:
        # 建表发生在 lifespan 启动事件里，此时尚未发出任何请求
        assert db_path.exists()
        assert test_client.get(TRIPS_URL).json()["data"] == []

    get_scheduler.cache_clear()
    get_memory_store.cache_clear()


def test_dependencies_share_one_store(client: TestClient) -> None:
    # 对话写入与 RESTful 读取必须是同一个库，否则建完行程列表还是空的
    assert get_scheduler()._memory is get_memory_store()


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("post", CHAT_URL, {"json": {"message": PLAN_MESSAGE}}),
        ("post", CHAT_URL, {"json": {"message": "你好"}}),
        ("get", TRIPS_URL, {}),
        ("delete", f"{TRIPS_URL}/tr_20261003_ffff", {}),
    ],
)
def test_response_body_shape(
    client: TestClient, method: str, url: str, kwargs: dict[str, object]
) -> None:
    body = getattr(client, method)(url, **kwargs).json()

    assert set(body) == {"code", "msg", "data"}


# ---------------------------------------------------------------- POST /api/chat


def test_chat_plans_trip(client: TestClient) -> None:
    body = client.post(CHAT_URL, json={"message": PLAN_MESSAGE}).json()

    assert body["code"] == 0
    assert body["data"]["action"] == ACTION_PLAN_TRIP
    assert body["data"]["trip"]["destination"] == "北京"
    assert body["data"]["trip"]["days"] == 3
    assert TRIP_ID_RE.match(body["data"]["trip"]["trip_id"])
    # 无 API Key → 模板降级，降级不占错误码（AC-5）
    assert body["data"]["trip"]["degraded"] is True
    assert body["data"]["trips"] == []


def test_chat_plan_is_persisted(client: TestClient) -> None:
    trip_id = create_trip(client)

    cards = client.get(TRIPS_URL).json()["data"]
    assert [card["trip_id"] for card in cards] == [trip_id]


def test_chat_lists_trips(client: TestClient) -> None:
    trip_id = create_trip(client)

    body = client.post(CHAT_URL, json={"message": "查看我的行程"}).json()

    assert body["data"]["action"] == ACTION_LIST_TRIPS
    assert [card["trip_id"] for card in body["data"]["trips"]] == [trip_id]


def test_chat_searches_trips(client: TestClient) -> None:
    trip_id = create_trip(client)

    body = client.post(CHAT_URL, json={"message": "搜索北京"}).json()

    assert body["data"]["action"] == "search_trips"
    assert [card["trip_id"] for card in body["data"]["trips"]] == [trip_id]


def test_chat_deletes_trip(client: TestClient) -> None:
    trip_id = create_trip(client)

    body = client.post(CHAT_URL, json={"message": f"删除 {trip_id}"}).json()

    assert body["data"]["action"] == ACTION_DELETE_TRIP
    assert client.get(TRIPS_URL).json()["data"] == []


def test_chat_answers_small_talk(client: TestClient) -> None:
    body = client.post(CHAT_URL, json={"message": "你好"}).json()

    assert body["code"] == 0
    assert body["data"]["action"] == ACTION_CHAT
    assert body["data"]["message"]
    assert body["data"]["trip"] is None


def test_chat_without_destination_returns_1002(client: TestClient) -> None:
    # 「去玩3天」命中 plan_trip 分支，但提取不出目的地
    response = client.post(CHAT_URL, json={"message": "去玩3天"})

    assert response.status_code == 200
    assert response.json()["code"] == 1002
    assert response.json()["data"] is None


@pytest.mark.parametrize("payload", [{}, {"message": ""}, {"message": "   "}, {"message": 123}])
def test_chat_rejects_invalid_payload(client: TestClient, payload: dict[str, object]) -> None:
    response = client.post(CHAT_URL, json=payload)

    assert response.status_code == InvalidRequestError.http_status
    assert response.json()["code"] == InvalidRequestError.code


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": "{不是 JSON", "headers": {"Content-Type": "application/json"}},
        {"content": "纯文本", "headers": {"Content-Type": "text/plain"}},
        {},
    ],
)
def test_chat_rejects_malformed_body(client: TestClient, kwargs: dict[str, object]) -> None:
    response = client.post(CHAT_URL, **kwargs)

    assert response.status_code == InvalidRequestError.http_status
    assert response.json()["code"] == InvalidRequestError.code


def test_chat_error_message_does_not_leak_details(client: TestClient) -> None:
    body = client.post(CHAT_URL, json={"message": ""}).json()

    # 业务文案定位到字段即可，不含 SQL / 路径 / 堆栈
    assert body["msg"].startswith("请求参数校验失败")
    assert "message" in body["msg"]
    for leaked in ("Traceback", "pydantic", "sqlite", "C:\\"):
        assert leaked not in body["msg"]


# ---------------------------------------------------------------- GET /api/trips


def test_list_trips_empty_returns_empty_list(client: TestClient) -> None:
    response = client.get(TRIPS_URL)

    assert response.status_code == 200
    assert response.json() == {"code": 0, "msg": "ok", "data": []}


def test_list_trips_returns_cards(client: TestClient) -> None:
    trip_id = create_trip(client)

    cards = client.get(TRIPS_URL).json()["data"]

    assert len(cards) == 1
    assert cards[0]["trip_id"] == trip_id
    assert cards[0]["destination"] == "北京"


def test_list_trips_newest_first(client: TestClient) -> None:
    first = create_trip(client, "帮我规划去北京玩3天")
    second = create_trip(client, "帮我规划去杭州玩2天")

    cards = client.get(TRIPS_URL).json()["data"]

    assert [card["trip_id"] for card in cards] == [second, first]


def test_list_trips_respects_limit(client: TestClient) -> None:
    create_trip(client, "帮我规划去北京玩3天")
    create_trip(client, "帮我规划去杭州玩2天")

    assert len(client.get(TRIPS_URL, params={"limit": 1}).json()["data"]) == 1


@pytest.mark.parametrize("limit", [0, -1, MAX_LIST_LIMIT + 1])
def test_list_trips_rejects_out_of_range_limit(client: TestClient, limit: int) -> None:
    response = client.get(TRIPS_URL, params={"limit": limit})

    assert response.status_code == InvalidRequestError.http_status
    assert response.json()["code"] == InvalidRequestError.code


@pytest.mark.parametrize("limit", ["不是数字", ""])
def test_list_trips_rejects_non_numeric_limit(client: TestClient, limit: str) -> None:
    response = client.get(TRIPS_URL, params={"limit": limit})

    assert response.status_code == InvalidRequestError.http_status


# ---------------------------------------------------------------- GET /api/trips/{id}


def test_get_trip_returns_full_record(client: TestClient) -> None:
    trip_id = create_trip(client)

    body = client.get(f"{TRIPS_URL}/{trip_id}").json()

    assert body["code"] == 0
    record = body["data"]
    assert record["trip_id"] == trip_id
    assert record["destination"] == "北京"
    assert record["intent_raw"] == PLAN_MESSAGE
    assert record["itinerary"]["destination"] == "北京"
    # 详情接口返回完整 TripPlan，三个增强字段一并带出
    assert record["itinerary"]["weather_report"]
    assert record["itinerary"]["cost_breakdown"]["days"] == 3
    assert record["itinerary"]["source"] == "template"


def test_get_missing_trip_returns_3001(client: TestClient) -> None:
    response = client.get(f"{TRIPS_URL}/tr_20261003_ffff")

    assert response.status_code == 200
    assert response.json()["code"] == TripNotFoundError.code
    assert response.json()["data"] is None


# ---------------------------------------------------------------- 出发日期全链路


def test_chat_accepts_start_date(client: TestClient) -> None:
    start = date.today() + timedelta(days=3)
    body = client.post(
        CHAT_URL, json={"message": PLAN_MESSAGE, "start_date": start.isoformat()}
    ).json()

    assert body["code"] == 0
    trip = body["data"]["trip"]
    assert [day["date"] for day in trip["daily"]] == [
        start.isoformat(),
        (start + timedelta(days=1)).isoformat(),
        (start + timedelta(days=2)).isoformat(),
    ]
    # 天气报告与出发日期对齐
    assert trip["weather_report"][0]["date"] == start.isoformat()


def test_chat_parses_start_date_from_text(client: TestClient) -> None:
    body = client.post(
        CHAT_URL, json={"message": "帮我规划去北京玩3天，10月25日出发"}
    ).json()

    assert body["data"]["trip"]["daily"][0]["date"].endswith("-10-25")


def test_chat_rejects_malformed_start_date(client: TestClient) -> None:
    response = client.post(
        CHAT_URL, json={"message": PLAN_MESSAGE, "start_date": "不是日期"}
    )

    assert response.status_code == InvalidRequestError.http_status
    assert response.json()["code"] == InvalidRequestError.code


# ---------------------------------------------------------------- 目的地校验


def test_chat_invalid_destination_returns_chat_guide(client: TestClient) -> None:
    body = client.post(CHAT_URL, json={"message": "帮我规划去西安玩3天"}).json()

    assert body["code"] == 0
    assert body["data"]["action"] == ACTION_CHAT
    assert "西安" in body["data"]["message"]
    assert body["data"]["trip"] is None
    # 无效目的地不生成、不落库
    assert client.get(TRIPS_URL).json()["data"] == []


# ---------------------------------------------------------------- DELETE /api/trips/{id}


def test_delete_trip_succeeds(client: TestClient) -> None:
    trip_id = create_trip(client)

    response = client.delete(f"{TRIPS_URL}/{trip_id}")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "msg": "ok",
        "data": {"trip_id": trip_id, "deleted": True},
    }
    assert client.get(TRIPS_URL).json()["data"] == []


def test_delete_missing_trip_returns_3001_over_http_200(client: TestClient) -> None:
    response = client.delete(f"{TRIPS_URL}/tr_20261003_ffff")

    assert response.status_code == 200  # 业务错误不借 HTTP 状态码表达
    assert response.json()["code"] == TripNotFoundError.code
    assert response.json()["data"] is None


def test_delete_missing_trip_message_has_no_internals(client: TestClient) -> None:
    msg = client.delete(f"{TRIPS_URL}/tr_20261003_ffff").json()["msg"]

    assert "不存在" in msg
    for leaked in ("SELECT", "DELETE", "sqlite", "trips.db", "Traceback"):
        assert leaked not in msg


def test_delete_twice_returns_3001(client: TestClient) -> None:
    trip_id = create_trip(client)
    client.delete(f"{TRIPS_URL}/{trip_id}")

    assert client.delete(f"{TRIPS_URL}/{trip_id}").json()["code"] == TripNotFoundError.code


# ---------------------------------------------------------------- 异常转换


def test_unexpected_error_maps_to_5000(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(trip_id: str) -> None:
        raise RuntimeError("数据库连接池炸了")

    monkeypatch.setattr(get_memory_store(), "delete_trip", boom)

    response = client.delete(f"{TRIPS_URL}/tr_20261003_ffff")

    assert response.status_code == AgentError.http_status
    assert response.json() == {"code": AgentError.code, "msg": "内部错误", "data": None}


def test_unexpected_error_message_is_generic(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(trip_id: str) -> None:
        raise RuntimeError("C:\\secret\\path\\db.sqlite 打开失败")

    monkeypatch.setattr(get_memory_store(), "delete_trip", boom)

    body = client.delete(f"{TRIPS_URL}/tr_20261003_ffff").json()

    assert "secret" not in body["msg"]
