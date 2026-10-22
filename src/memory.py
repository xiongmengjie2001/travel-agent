"""MemoryStore：行程方案的 SQLite 持久化存取。

FR-2（行程记忆管理）的唯一实现者。不解析用户意图、不生成行程、不做格式
转换。输入输出均为 Pydantic 模型（CLAUDE.md 禁止裸 dict）。

并发配置（WAL 模式、busy_timeout、写操作串行化）见
docs/module-sdd/memory.md 第 5 节。

⚠️ 连接管理的实现说明：SDD 要求「不复用跨线程连接」。本模块据此**不在
实例上持有长连接**，每次操作经 `_session()` 取一个新连接、用毕关闭 ——
FastAPI 路由以同步 `def` 跑在线程池里，持长连接反而会在跨线程复用时抛
`ProgrammingError`。PRAGMA 每次连接显式设置（SDD 第 5 节已预期这一点）。

SDD 来源：本模块的 SDD 产出自**第 18 节**，本课直接复用 —— 原始产出归档于
docs/sdd/memory-store.md，现行版本为 docs/module-sdd/memory.md（两者内容一致，
后者是唯一被维护的一份）。

规格见 docs/module-sdd/memory.md。
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from src.exceptions import InvalidRequestError, MemoryStoreError, TripNotFoundError
from src.models import TripPlan, TripRecord

DEFAULT_LIST_LIMIT = 20
MIN_LIST_LIMIT = 1
MAX_LIST_LIMIT = 100
BUSY_TIMEOUT_MS = 5000

TRIP_ID_PREFIX = "tr_"
TRIP_ID_RANDOM_BYTES = 2  # → 4 位十六进制，如 tr_20261003_8f3a

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    trip_id     TEXT    PRIMARY KEY,
    created_at  TEXT    NOT NULL,
    destination TEXT    NOT NULL,
    days        INTEGER NOT NULL,
    budget      REAL,
    intent_raw  TEXT    NOT NULL,
    itinerary   TEXT    NOT NULL,
    degraded    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trips_created_at  ON trips (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trips_destination ON trips (destination);
"""

_INSERT_SQL = (
    "INSERT INTO trips "
    "(trip_id, created_at, destination, days, budget, intent_raw, itinerary, degraded) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_COLUMNS = (
    "trip_id, created_at, destination, days, budget, intent_raw, itinerary, degraded"
)
# created_at 为同一时刻时用 rowid 兜底，保证倒序结果稳定。
_ORDER_BY = "ORDER BY created_at DESC, rowid DESC"
_LIKE_ESCAPE = "\\"

# 写操作（save_trip / delete_trip）经此锁串行化，
# 把 busy_timeout 从「兜底」降为「保险」。
_WRITE_LOCK = threading.Lock()


class MemoryStore:
    """行程持久化。构造时初始化库结构，每次操作取新连接。"""

    def __init__(self, db_path: str | Path, *, read_only: bool = False) -> None:
        """校验并初始化库结构（WAL 等 PRAGMA 在每次连接时设置）。

        Args:
            db_path: 数据库文件路径，由配置注入（`MEMORY_DB_PATH` 环境变量），
                **不得**在模块内硬编码。
            read_only: 只读模式，跳过建表。

        Raises:
            MemoryStoreError: 库文件无法打开或建表 / PRAGMA 执行失败。
        """
        self._db_path = Path(db_path)
        self._read_only = read_only
        if not read_only:
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise MemoryStoreError(f"记忆库目录不可创建: {exc}") from exc
        with self._session() as conn:
            if not read_only:
                try:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                except sqlite3.Error as exc:
                    raise MemoryStoreError(f"初始化记忆库失败: {exc}") from exc

    # ------------------------------------------------------------ 连接管理

    def _session(self) -> Iterator[sqlite3.Connection]:
        """取一个已设好 PRAGMA 的新连接，用毕关闭。"""
        return _connect_session(self._db_path, read_only=self._read_only)

    # ------------------------------------------------------------ 写操作

    def save_trip(self, itinerary: TripPlan, *, intent_raw: str = "") -> TripRecord:
        """生成 trip_id 与 created_at 后落库。

        Args:
            itinerary: 待保存的行程方案。
            intent_raw: 用户原始输入，用于检索命中时展示。`TripPlan` 不含该
                字段（它是解析前的文本），故由调用方经本参数注入；未提供时
                回退为 `itinerary.destination`（列定义为 NOT NULL）。

        Returns:
            含 trip_id 的 TripRecord。

        Raises:
            MemoryStoreError: 序列化失败、磁盘满或写锁超时。
        """
        now = datetime.now(timezone.utc)
        trip_id = _generate_trip_id(now)
        try:
            payload = itinerary.model_dump_json()
        except Exception as exc:  # pydantic 序列化异常统一收敛
            raise MemoryStoreError(f"行程无法序列化: {exc}") from exc

        try:
            with _WRITE_LOCK, self._session() as conn:
                conn.execute(
                    _INSERT_SQL, _to_insert_row(trip_id, now, itinerary, intent_raw, payload)
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"保存行程失败: {exc}") from exc

        return TripRecord(
            trip_id=trip_id,
            created_at=now,
            destination=itinerary.destination,
            days=itinerary.days,
            budget=itinerary.budget,
            intent_raw=intent_raw or itinerary.destination,
            itinerary=itinerary,
            degraded=itinerary.degraded,
        )

    def delete_trip(self, trip_id: str) -> bool:
        """删除指定行程。

        Returns:
            删除成功为 True。

        Raises:
            TripNotFoundError: trip_id 不存在（不返回 False）。
            MemoryStoreError: SQLite 报错。
        """
        try:
            with _WRITE_LOCK, self._session() as conn:
                cursor = conn.execute("DELETE FROM trips WHERE trip_id = ?", (trip_id,))
                removed = cursor.rowcount
                conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"删除行程失败: {exc}") from exc

        if removed == 0:
            raise TripNotFoundError(f"行程 {trip_id} 不存在")
        return True

    # ------------------------------------------------------------ 读操作

    def list_trips(self, limit: int = DEFAULT_LIST_LIMIT) -> list[TripRecord]:
        """按 created_at 倒序列出历史行程。

        Args:
            limit: 返回条数上限，范围 1–100。

        Raises:
            InvalidRequestError: limit 越界（<1 或 >100）。
            MemoryStoreError: SQLite 报错。
        """
        _validate_limit(limit)
        sql = f"SELECT {_SELECT_COLUMNS} FROM trips {_ORDER_BY} LIMIT ?"
        try:
            with self._session() as conn:
                rows = conn.execute(sql, (limit,)).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"查询行程列表失败: {exc}") from exc
        return _rows_to_records(rows)

    def get_trip(self, trip_id: str) -> TripRecord:
        """按 trip_id 取单条完整行程（含原始输入与增强字段）。

        Raises:
            InvalidRequestError: trip_id 为空白。
            TripNotFoundError: trip_id 不存在。
            MemoryStoreError: SQLite 报错或 payload 无法反序列化。
        """
        cleaned = (trip_id or "").strip()
        if not cleaned:
            raise InvalidRequestError("行程 ID 不能为空白")

        sql = f"SELECT {_SELECT_COLUMNS} FROM trips WHERE trip_id = ?"
        try:
            with self._session() as conn:
                row = conn.execute(sql, (cleaned,)).fetchone()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"查询行程详情失败: {exc}") from exc

        if row is None:
            raise TripNotFoundError(f"行程 {cleaned} 不存在")
        return _rows_to_records([row])[0]

    def search_trips(self, keyword: str, limit: int = DEFAULT_LIST_LIMIT) -> list[TripRecord]:
        """对 destination 与 intent_raw 做关键词检索。

        Args:
            keyword: 检索词，去空白后不得为空。
            limit: 返回条数上限，范围 1–100。

        Raises:
            InvalidRequestError: keyword 为空白，或 limit 越界。
            MemoryStoreError: SQLite 报错。
        """
        _validate_limit(limit)
        cleaned = keyword.strip()
        if not cleaned:
            raise InvalidRequestError("检索关键词不能为空白")

        pattern = f"%{_escape_like(cleaned)}%"
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM trips "
            f"WHERE destination LIKE ? ESCAPE '{_LIKE_ESCAPE}' "
            f"   OR intent_raw  LIKE ? ESCAPE '{_LIKE_ESCAPE}' "
            f"{_ORDER_BY} LIMIT ?"
        )
        try:
            with self._session() as conn:
                rows = conn.execute(sql, (pattern, pattern, limit)).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"检索行程失败: {exc}") from exc
        return _rows_to_records(rows)


# ---------------------------------------------------------------- 模块级辅助


@contextmanager
def _connect_session(db_path: Path, *, read_only: bool) -> Iterator[sqlite3.Connection]:
    """建立连接、设置 PRAGMA，退出时必定关闭。

    Args:
        db_path: 库文件路径。
        read_only: 以 `mode=ro` URI 打开，跳过写相关的 PRAGMA。

    Raises:
        MemoryStoreError: 连接或 PRAGMA 执行失败。
    """
    try:
        if read_only:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        raise MemoryStoreError(f"打开记忆库失败: {exc}") from exc

    try:
        conn.row_factory = sqlite3.Row
        if not read_only:
            # WAL 为持久设置，但库文件可能被替换，故每次连接显式设置。
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as exc:
        conn.close()
        raise MemoryStoreError(f"设置记忆库 PRAGMA 失败: {exc}") from exc

    try:
        yield conn
    finally:
        conn.close()


def _to_insert_row(
    trip_id: str,
    now: datetime,
    itinerary: TripPlan,
    intent_raw: str,
    payload: str,
) -> tuple[str, str, str, int, float | None, str, str, int]:
    """TripPlan → `trips` 表的一行，列顺序与 `_INSERT_SQL` 严格一致。

    单独成函数是为了让「一行的内容」与「怎么写入」分开：改列顺序时只需要看
    这里，`save_trip` 的写锁与异常收敛逻辑不受影响。

    `payload` 由调用方传入而非在此序列化 —— 序列化失败要归入 `MemoryStoreError`
    的独立分支，不能与 `sqlite3.Error` 混在一起。
    """
    return (
        trip_id,
        now.isoformat(),
        itinerary.destination,
        itinerary.days,
        itinerary.budget,
        intent_raw or itinerary.destination,
        payload,
        int(itinerary.degraded),
    )


def _generate_trip_id(now: datetime) -> str:
    """生成 `<前缀><YYYYMMDD>_<4位随机十六进制>` 形式的 trip_id。"""
    return f"{TRIP_ID_PREFIX}{now:%Y%m%d}_{secrets.token_hex(TRIP_ID_RANDOM_BYTES)}"


def _validate_limit(limit: int) -> None:
    """校验 limit 落在 1–100。

    Raises:
        InvalidRequestError: limit 越界。
    """
    if limit < MIN_LIST_LIMIT or limit > MAX_LIST_LIMIT:
        raise InvalidRequestError(
            f"limit 必须在 {MIN_LIST_LIMIT}–{MAX_LIST_LIMIT} 之间，收到 {limit}"
        )


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % / _ 变成通配符。"""
    escaped = keyword.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
    escaped = escaped.replace("%", _LIKE_ESCAPE + "%")
    return escaped.replace("_", _LIKE_ESCAPE + "_")


def _rows_to_records(rows: list[sqlite3.Row]) -> list[TripRecord]:
    """SQLite 行 → TripRecord。

    Raises:
        MemoryStoreError: itinerary 列不是合法 JSON 或与 TripPlan 不符。
    """
    records: list[TripRecord] = []
    for row in rows:
        try:
            itinerary = TripPlan.model_validate_json(row["itinerary"])
        except Exception as exc:
            raise MemoryStoreError(
                f"行程 {row['trip_id']} 的 payload 无法反序列化: {exc}"
            ) from exc
        try:
            records.append(
                TripRecord(
                    trip_id=row["trip_id"],
                    created_at=row["created_at"],
                    destination=row["destination"],
                    days=row["days"],
                    budget=row["budget"],
                    intent_raw=row["intent_raw"],
                    itinerary=itinerary,
                    degraded=bool(row["degraded"]),
                )
            )
        except Exception as exc:
            raise MemoryStoreError(f"行程 {row['trip_id']} 的记录不完整: {exc}") from exc
    return records


__all__ = [
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "MIN_LIST_LIMIT",
    "MemoryStore",
]
