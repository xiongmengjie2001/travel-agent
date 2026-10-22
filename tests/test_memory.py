"""MemoryStore 单测。

对应 SDD docs/module-sdd/memory.md 第 7 节验收清单。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import date
from pathlib import Path

import pytest

from src.exceptions import InvalidRequestError, MemoryStoreError, TripNotFoundError
from src.memory import MAX_LIST_LIMIT, MemoryStore
from src.models import CostResult, DailyPlan, Spot, TripPlan, TripRecord, WeatherInfo

TRIP_ID_RE = re.compile(r"^tr_\d{8}_[0-9a-f]{4}$")


def make_plan(
    destination: str = "北京",
    days: int = 2,
    *,
    degraded: bool = False,
    budget: float | None = None,
) -> TripPlan:
    return TripPlan(
        destination=destination,
        days=days,
        budget=budget,
        total_cost=600.0 * days,
        daily=[
            DailyPlan(
                day_index=index + 1,
                spots=[Spot(name=f"{destination}景点{index + 1}")],
                estimated_cost=600.0,
            )
            for index in range(days)
        ],
        degraded=degraded,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "trips.db"


@pytest.fixture
def store(db_path: Path) -> MemoryStore:
    return MemoryStore(db_path)


# ---------------------------------------------------------------- save / list


def test_save_returns_record_with_generated_id(store: MemoryStore) -> None:
    record = store.save_trip(make_plan())

    assert isinstance(record, TripRecord)
    assert TRIP_ID_RE.match(record.trip_id)
    assert record.destination == "北京"
    assert record.days == 2


def test_saved_trip_is_listable(store: MemoryStore) -> None:
    record = store.save_trip(make_plan())

    listed = store.list_trips()
    assert [item.trip_id for item in listed] == [record.trip_id]


def test_list_returns_newest_first(store: MemoryStore) -> None:
    saved = [store.save_trip(make_plan(destination=f"城市{index}")).trip_id for index in range(3)]

    assert [item.trip_id for item in store.list_trips()] == list(reversed(saved))


def test_list_respects_limit(store: MemoryStore) -> None:
    for index in range(3):
        store.save_trip(make_plan(destination=f"城市{index}"))

    assert len(store.list_trips(limit=2)) == 2


def test_list_empty_returns_empty_list(store: MemoryStore) -> None:
    assert store.list_trips() == []


@pytest.mark.parametrize("limit", [0, -1, MAX_LIST_LIMIT + 1])
def test_list_rejects_out_of_range_limit(store: MemoryStore, limit: int) -> None:
    with pytest.raises(InvalidRequestError, match="limit"):
        store.list_trips(limit=limit)


def test_intent_raw_defaults_to_destination(store: MemoryStore) -> None:
    record = store.save_trip(make_plan(destination="杭州"))

    assert record.intent_raw == "杭州"


def test_intent_raw_is_persisted_verbatim(store: MemoryStore) -> None:
    raw = "帮我规划去成都玩 3 天"
    store.save_trip(make_plan(destination="成都"), intent_raw=raw)

    assert store.list_trips()[0].intent_raw == raw


def test_degraded_flag_round_trips(store: MemoryStore) -> None:
    store.save_trip(make_plan(degraded=True))

    assert store.list_trips()[0].degraded is True


def test_budget_round_trips(store: MemoryStore) -> None:
    store.save_trip(make_plan(budget=5000.0))

    assert store.list_trips()[0].budget == 5000.0


def test_itinerary_is_stored_as_unescaped_utf8(store: MemoryStore, db_path: Path) -> None:
    store.save_trip(make_plan(destination="北京"))

    with sqlite3.connect(db_path) as conn:
        payload = conn.execute("SELECT itinerary FROM trips").fetchone()[0]

    assert "北京" in payload  # ensure_ascii=True 会存成 北京


# ---------------------------------------------------------------- search


def test_search_hits_destination(store: MemoryStore) -> None:
    store.save_trip(make_plan(destination="北京"))
    store.save_trip(make_plan(destination="上海"))

    assert [item.destination for item in store.search_trips("北京")] == ["北京"]


def test_search_hits_intent_raw(store: MemoryStore) -> None:
    store.save_trip(make_plan(destination="杭州"), intent_raw="想去西湖看看美食")

    assert len(store.search_trips("美食")) == 1


def test_search_without_match_returns_empty_list(store: MemoryStore) -> None:
    store.save_trip(make_plan(destination="北京"))

    assert store.search_trips("三亚") == []


def test_search_escapes_like_wildcards(store: MemoryStore) -> None:
    store.save_trip(make_plan(destination="北京"))

    assert store.search_trips("%") == []
    assert store.search_trips("_") == []


@pytest.mark.parametrize("keyword", ["", "   ", "\t"])
def test_search_rejects_blank_keyword(store: MemoryStore, keyword: str) -> None:
    with pytest.raises(InvalidRequestError, match="关键词"):
        store.search_trips(keyword)


# ---------------------------------------------------------------- get


def test_get_trip_returns_saved_record(store: MemoryStore) -> None:
    saved = store.save_trip(make_plan(), intent_raw="去北京玩2天")

    loaded = store.get_trip(saved.trip_id)

    assert loaded == saved
    assert loaded.intent_raw == "去北京玩2天"


def test_get_trip_round_trips_enhanced_fields(store: MemoryStore) -> None:
    plan = TripPlan(
        destination="北京",
        days=1,
        total_cost=600.0,
        daily=[
            DailyPlan(day_index=1, spots=[Spot(name="故宫")], estimated_cost=600.0)
        ],
        weather_report=[WeatherInfo(date=date(2026, 10, 25), condition="晴")],
        cost_breakdown=CostResult(days=1, total=600.0),
        source="llm",
    )
    saved = store.save_trip(plan)

    loaded = store.get_trip(saved.trip_id)

    assert loaded.itinerary.weather_report == plan.weather_report
    assert loaded.itinerary.cost_breakdown == plan.cost_breakdown
    assert loaded.itinerary.source == "llm"


@pytest.mark.parametrize("trip_id", ["", "   ", "\t"])
def test_get_trip_rejects_blank_id(store: MemoryStore, trip_id: str) -> None:
    with pytest.raises(InvalidRequestError, match="不能为空白"):
        store.get_trip(trip_id)


def test_get_missing_trip_raises_not_found(store: MemoryStore) -> None:
    with pytest.raises(TripNotFoundError):
        store.get_trip("tr_20261003_ffff")


# ---------------------------------------------------------------- delete


def test_delete_removes_record(store: MemoryStore) -> None:
    trip_id = store.save_trip(make_plan()).trip_id

    assert store.delete_trip(trip_id) is True
    assert store.list_trips() == []


def test_delete_missing_raises_not_found(store: MemoryStore) -> None:
    with pytest.raises(TripNotFoundError):
        store.delete_trip("tr_20261003_ffff")


# ---------------------------------------------------------------- 健壮性


def test_corrupt_payload_raises_memory_store_error(store: MemoryStore, db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trips (trip_id, created_at, destination, days, budget,"
            " intent_raw, itinerary, degraded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tr_20261003_0001", "2026-10-03T00:00:00+00:00", "北京", 1, None, "x", "{坏数据", 0),
        )
        conn.commit()

    with pytest.raises(MemoryStoreError, match="反序列化"):
        store.list_trips()


def test_wal_mode_is_enabled(store: MemoryStore, db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_read_only_store_can_read(store: MemoryStore, db_path: Path) -> None:
    store.save_trip(make_plan())
    reader = MemoryStore(db_path, read_only=True)

    assert len(reader.list_trips()) == 1


def test_read_only_store_rejects_write(store: MemoryStore, db_path: Path) -> None:
    store.save_trip(make_plan())
    reader = MemoryStore(db_path, read_only=True)

    with pytest.raises(MemoryStoreError):
        reader.save_trip(make_plan())


def test_read_only_store_on_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MemoryStoreError):
        MemoryStore(tmp_path / "不存在.db", read_only=True)


def test_missing_parent_directory_is_created(tmp_path: Path) -> None:
    nested = tmp_path / "data" / "nested" / "trips.db"
    store = MemoryStore(nested)

    store.save_trip(make_plan())
    assert nested.exists()


def test_concurrent_saves_all_succeed(db_path: Path) -> None:
    store = MemoryStore(db_path)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            store.save_trip(make_plan(destination=f"城市{index}"))
        except Exception as exc:  # noqa: BLE001 - 线程内异常收集后再断言
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(store.list_trips(limit=100)) == 10


# ------------------------------------------------- 异常路径（注入故障，不依赖真实故障）


def test_unusable_parent_directory_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("我不是目录", encoding="utf-8")

    with pytest.raises(MemoryStoreError, match="目录"):
        MemoryStore(blocker / "sub" / "trips.db")


def test_schema_init_failure_raises(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.memory._SCHEMA", "CREATE TABLE (")

    with pytest.raises(MemoryStoreError, match="初始化"):
        MemoryStore(db_path)


def test_serialization_failure_raises(store: MemoryStore) -> None:
    class BrokenPlan:
        """只走到序列化那一步就失败，故无需具备 TripPlan 的其余字段。"""

        def model_dump_json(self) -> str:
            raise ValueError("字段里有不可序列化的对象")

    with pytest.raises(MemoryStoreError, match="序列化"):
        store.save_trip(BrokenPlan())  # type: ignore[arg-type]


def test_pragma_failure_raises(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BadConnection:
        """execute 一律失败，模拟 PRAGMA 执行异常。"""

        row_factory: object = None

        def execute(self, *args: object, **kwargs: object) -> object:
            raise sqlite3.OperationalError("pragma 挂了")

        def close(self) -> None:
            return None

    monkeypatch.setattr("src.memory.sqlite3.connect", lambda *a, **k: BadConnection())

    with pytest.raises(MemoryStoreError, match="PRAGMA"):
        MemoryStore(db_path)


@pytest.mark.parametrize("operation", ["list", "search", "delete"])
def test_sqlite_errors_are_wrapped(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    def failing_session(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_session", failing_session)

    with pytest.raises(MemoryStoreError):
        if operation == "list":
            store.list_trips()
        elif operation == "search":
            store.search_trips("北京")
        else:
            store.delete_trip("tr_20261003_ffff")


def test_incomplete_row_raises(store: MemoryStore, db_path: Path) -> None:
    payload = make_plan().model_dump_json()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trips (trip_id, created_at, destination, days, budget,"
            " intent_raw, itinerary, degraded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            # destination 为空串 → 不满足 TripRecord.min_length=1
            ("tr_20261003_0002", "2026-10-03T00:00:00+00:00", "", 1, None, "x", payload, 0),
        )
        conn.commit()

    with pytest.raises(MemoryStoreError, match="不完整"):
        store.list_trips()
