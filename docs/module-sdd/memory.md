# SDD：记忆管理模块（MemoryStore）

> 对应 PRD [FR-2](../PRD.md)、系统设计 [2.4 节](../system-design.md)
> 版本：v1.0
> 日期：2026-10-03
> 位置：`src/memory.py`

## 1. 功能描述

负责行程方案的持久化存取，是 FR-2（行程记忆管理）的唯一实现者。

- 生成行程后自动落库，返回带 `trip_id` 的记录
- 支持按时间倒序列出历史行程
- 支持按关键词检索历史行程
- 支持按 `trip_id` 获取单条完整行程
- 支持按 `trip_id` 删除行程

**边界**：本模块不解析用户意图、不生成行程、不做格式转换（转换属 Bridge）。输入输出均为 Pydantic 模型（CLAUDE.md 禁止裸 dict）。

## 2. 接口签名

```python
class MemoryStore:
    def save_trip(self, itinerary: TripPlan, *, intent_raw: str = "") -> TripRecord: ...
    def list_trips(self, limit: int = 20) -> list[TripRecord]: ...
    def get_trip(self, trip_id: str) -> TripRecord: ...
    def search_trips(self, keyword: str, limit: int = 20) -> list[TripRecord]: ...
    def delete_trip(self, trip_id: str) -> bool: ...
```

| 方法 | 输入 | 输出 | 说明 |
| --- | --- | --- | --- |
| `save_trip` | `TripPlan`，`intent_raw: str = ""` | `TripRecord` | 生成 `trip_id` 与 `created_at` 后落库；`intent_raw` 留空时回落为 `destination` |
| `list_trips` | `limit: int = 20` | `list[TripRecord]` | 按 `created_at` 倒序；`limit` 范围 1–100 |
| `get_trip` | `trip_id: str` | `TripRecord` | 单条完整记录，含 `intent_raw` 与完整 `itinerary`（含增强字段） |
| `search_trips` | `keyword: str`, `limit` | `list[TripRecord]` | `keyword` 去空白后不得为空 |
| `delete_trip` | `trip_id: str` | `bool` | 删除成功 `True`；不存在抛 `TripNotFoundError` |

> **`intent_raw` 参数的补充说明（实现期修正，v1.0 → v1.1）**
>
> 原签名 `save_trip(itinerary: TripPlan)` 与本文档 §3 自相矛盾：表结构中
> `intent_raw` 为 `NOT NULL`，且 §3 明确 `search_trips` 要对该列做 `LIKE` 匹配
> （FR-2 要求「按关键词找回当时的说法」）；而 `TripPlan` 不包含用户原始语料，
> 仅凭 `itinerary` 无法填出该列。
>
> 故补充**仅限关键字**的可选参数 `intent_raw`：由调用方
> （[Scheduler](./scheduler.md) `_handle_plan_trip`，传入 `Intent.raw_text`）提供，
> 留空时回落为 `itinerary.destination`，保证既有调用方不必改动。

**构造**：

```python
class MemoryStore:
    def __init__(self, db_path: str | Path, *, read_only: bool = False) -> None: ...
```

`db_path` 由配置注入（`MEMORY_DB_PATH` 环境变量），**不得**在模块内硬编码（CLAUDE.md 禁止事项）。

## 3. 数据模型

`TripRecord` 定义在共享模型层 [src/models/trip.py](../../src/models/trip.py)，本模块**不重复定义**（见 [models SDD](../sdd/models.md)）。本模块的职责是它与 SQLite 行之间的映射。

### 表结构

```sql
CREATE TABLE IF NOT EXISTS trips (
    trip_id     TEXT    PRIMARY KEY,
    created_at  TEXT    NOT NULL,           -- ISO8601 UTC
    destination TEXT    NOT NULL,
    days        INTEGER NOT NULL,
    budget      REAL,
    intent_raw  TEXT    NOT NULL,
    itinerary   TEXT    NOT NULL,           -- TripPlan 的 JSON 序列化
    degraded    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trips_created_at  ON trips (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trips_destination ON trips (destination);
```

`trip_id` 生成规则：`tr_<YYYYMMDD>_<4位随机十六进制>`，如 `tr_20261003_8f3a`。

**检索实现**：`search_trips` 对 `destination` 与 `intent_raw` 做 `LIKE '%keyword%'` 匹配。当前不引入 FTS5 —— 数据量为个人级别，`LIKE` 配合索引足够；若后续行数超过 10 万再评估。

## 4. 外部依赖

| 依赖 | 用途 | 备注 |
| --- | --- | --- |
| SQLite（标准库 `sqlite3`） | 持久化存储 | 无外部服务，随应用启动初始化 |

## 5. 并发配置（WAL 模式）

NFR-1 要求「支持并发 10 个用户」，而 SQLite 默认的 journal 模式会让写操作阻塞所有读操作。**连接建立后必须立即执行以下 PRAGMA**：

```python
conn.execute("PRAGMA journal_mode = WAL")      # 读写不互斥
conn.execute("PRAGMA busy_timeout = 5000")     # 写锁等待 5s，而非立即报错
conn.execute("PRAGMA synchronous = NORMAL")    # WAL 下的安全/性能平衡点
conn.execute("PRAGMA foreign_keys = ON")
```

### 决策与理由

- **WAL**：写事务进行时读操作仍可并发，这是满足 10 并发的前提。`journal_mode=WAL` 为持久设置，写入数据库文件后重连仍生效，但每次连接仍显式设置以防库文件被替换。
- **`busy_timeout=5000`**：WAL 只解除了读写的互斥，**写与写之间仍然是串行的**。多个写并发时后来者会收到 `database is locked`；设置超时让它排队等待而非直接失败。
- **`synchronous=NORMAL`**：WAL 模式下该级别在崩溃时最多丢失最后一个事务，不会损坏数据库。对行程数据可接受。

### 连接管理

- FastAPI 路由使用 **同步 `def`**（跑在线程池），避免 `sqlite3` 阻塞事件循环。
- 每个请求从连接池取一个连接，用毕归还；**不复用跨线程连接**（`sqlite3` 连接默认绑定创建线程）。
- 写操作（`save_trip` / `delete_trip`）经进程内 `threading.Lock` 串行化，把 `busy_timeout` 从「兜底」降为「保险」——预期不会真正触发等待。

> WAL 会额外产生 `-wal` 与 `-shm` 两个附属文件，需加入 `.gitignore`。

## 6. 异常处理

| 场景 | 异常 | 对外 code |
| --- | --- | --- |
| `trip_id` 不存在 | `TripNotFoundError` | 3001 |
| `get_trip` 的 `trip_id` 为空白 | `InvalidRequestError` | 1001 |
| `keyword` 为空白 | `InvalidRequestError` | 1001 |
| `limit` 越界（<1 或 >100） | `InvalidRequestError` | 1001 |
| 磁盘满 / 库文件损坏 / 写锁超时 | `MemoryStoreError` | 3002 |
| `itinerary` 无法序列化 / 反序列化 | `MemoryStoreError` | 3002 |

异常定义见 [src/exceptions.py](../../src/exceptions.py)（规格见 [docs/schemas/api-response.md](../schemas/api-response.md)）。本模块只抛 `AgentError` 子类，不抛 `HTTPException`。

**不吞异常**：`sqlite3.Error` 一律包装为 `MemoryStoreError` 并保留原始信息在 `__cause__`，禁止静默返回空列表 —— 那会让「查询失败」和「确实没有记录」在前端无法区分。

## 7. 验收清单

代码完成后逐条对照：

- [ ] `save_trip()` 返回的 `TripRecord.trip_id` 非空，且再次 `list_trips()` 能查到（AC-2）
- [ ] `list_trips()` 按 `created_at` 严格倒序
- [ ] `search_trips("北京")` 能命中 `destination="北京"` 与 `intent_raw` 含「北京」的记录（FR-2）
- [ ] `delete_trip()` 后该 `trip_id` 在 `list_trips()` 中消失（AC-2）
- [ ] `get_trip()` 返回的 `itinerary` 含 `weather_report` / `cost_breakdown` / `source`，与落库前一致
- [ ] `get_trip()` 的 `trip_id` 为空白时抛 `InvalidRequestError`，不存在时抛 `TripNotFoundError`
- [ ] `delete_trip()` 不存在的 id 抛 `TripNotFoundError`，而非返回 `False`
- [ ] 10 线程并发 `save_trip()` 全部成功，无 `database is locked`
- [ ] 开启 WAL 后确认 `PRAGMA journal_mode` 返回 `wal`
- [ ] `degraded` 字段正确传递，降级行程落库后读回仍为 `True`
- [ ] `tests/test_memory.py` 覆盖率 100%（核心模块，CLAUDE.md 要求）
