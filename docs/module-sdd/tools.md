# ToolCenter SDD

> 文件：`src/tools.py`
> 项目：travel-agent2
> MCP 数据源规格见 [mcp.md](./mcp.md)（高德 MCP 客户端、工具映射、降级与调用链）

## 0. 实现状态说明

### v4.0 变更（2026-10-21）：接入高德 MCP 数据源

`weather` / `attractions` / `route` 三个工具改为 **MCP 优先 + Mock 降级**：

1. 新增 `src/mcp_client.py::AmapMCPClient`，封装高德 MCP 的 JSON-RPC 握手与
   `tools/call`；ToolCenter 构造时按 `AMAP_MCP_URL` / `AMAP_MCP_KEY` 自动构建，
   也可经 `ToolCenter(mcp_client=...)` 注入测试替身。
2. 三个工具任一 MCP 调用失败（网络 / 超时 / 非 2xx / 响应不符契约）时回落同语义
   Mock，**不中断行程生成**；未配置 `AMAP_MCP_URL` 时直接走 Mock，不出网。
3. `cost` **始终本地估算**，不接 MCP。
4. 新增 `ToolCenter.validate_destination()`：MCP 地理编码 → Mock 城市白名单 →
   判无效；`AmapMCPClient.city_exists()` 把所有异常收敛为 `False`。
5. 移除旧的 `MCP_WEATHER_URL` 自定义 REST 天气源，由高德 MCP 取代。

详细规格（工具映射表、JSON-RPC 报文、SSE 兼容、花费分项来源、调用链）见
[mcp.md](./mcp.md)。

### v3.0 变更（2026-10-21）：调用全面异步化 + Function Calling 工具描述

1. **注册表异步化**：`ToolCenter.call()` 改为 `async`，内部同时适配同步与
   异步工具（`inspect.isawaitable`），调用方统一 `await`。`weather` 的 MCP
   分支改用 `httpx.AsyncClient`；`attractions` / `route` / `cost` 仍为同步
   实现，由注册表适配。
2. **新增 `TOOL_DEFINITIONS`**：4 个工具的 OpenAI Function Calling 描述
   （名称与注册表严格一致），供 ReasoningEngine 在 `tools` 参数中携带。
3. **花费模型升级为四段分项**：门票 / 市内交通 / 住宿 / 餐饮，四项之和恒等于
   `total`。拆分比例：住宿 45% / 餐饮 25% / 市内交通 15% / 门票取余额。
4. **路线新增 `total_distance_km`**：步行按每跳 1.5km、公共交通按每跳 5km
   估算，总距离 = 跳数 × 单跳距离。

### v2.0 变更（2026-10-03）：`weather` 接入 MCP 数据源

`weather` 由「纯 Mock」升级为**双数据源**：设了环境变量 `MCP_WEATHER_URL` 时调
MCP 天气服务（HTTP），未设时回落到模块顶部静态表。SDD §3 声明的 `httpx` 与
`MCP_WEATHER_URL` 至此成为**实际依赖**，§2 声明「数据源 = MCP 天气服务」成立。

未配置 `MCP_WEATHER_URL` 时**不发起任何网络请求** —— 本地开发与测试保持零外部
依赖，既有静态表用例无需改动。

**对接契约**（§3 原只声明「HTTP + `MCP_WEATHER_URL`」未定义报文格式，此处约定）：

```
GET {MCP_WEATHER_URL}?city=北京&days=3
→ 200 {"days": [{"date": "2026-10-03", "condition": "晴",
                 "temp_high": 28.0, "temp_low": 22.0},
                ...]}
```

- `date` 可缺省，缺省时按「自今天起第 N 天」推算；`temp_high` / `temp_low` 可缺省为 `null`；
- `days` 数组条数 **< 请求天数**、条目不是对象、`condition` 缺失等一律视为**不符契约**，抛 `ToolCallError`（2002）交由调用方降级 —— 不返回半截天气；
- 超时按 §3 建议取 10s。

> ⚠️ **v4.0 已废弃**：上述 `MCP_WEATHER_URL` 自定义 REST 契约由
> [mcp.md](./mcp.md) 的高德 MCP 协议取代，代码不再读取该环境变量。本节仅作
> 历史记录保留。

### 当前差异

| 工具 | §2 的目标数据源 | 当前实现 | 状态 |
| --- | --- | --- | --- |
| `weather` | 高德 MCP `maps_weather` | v4.0 起 MCP 优先；未配置或失败时回落 `WEATHER_PATTERN` / `DEFAULT_WEATHER_PATTERN` | **已接入** |
| `attractions` | 高德 MCP `maps_text_search` | v4.0 起 MCP 优先；未配置或失败时回落 `ATTRACTIONS` / `DEFAULT_ATTRACTIONS` | **已接入** |
| `route` | 高德 MCP `maps_geo` + `maps_direction_driving` | v4.0 起 MCP 优先；未配置或失败时回落本地跳数估算 | **已接入** |
| `cost` | 本地计算 | **已按 §2 实现**，不接 MCP | — |

约束：

1. **全部假数据集中在模块顶部常量区**，函数体内不得出现字面量假数据；
2. 数据源切换只发生在 ToolCenter 的 `_weather` / `_attractions` / `_route` 包装层，
   接口签名与返回模型不变，调用方（ReasoningEngine）无感；
3. 未配置 `AMAP_MCP_URL` 时四个工具**均不发起网络请求**；`httpx` 是 MCP 客户端的
   实际依赖（见 [mcp.md](./mcp.md) §3）；
4. MCP 失败一律降级为 Mock，具体矩阵见 [mcp.md](./mcp.md) §4.1。

**数据稳定性**：`weather` 的行下标取 `日期.toordinal() % 7`，使 `(城市, 日期) → 天气` 成为纯函数；同城市同日期任何时候调用结果恒定，且日期每推进一天取表中下一行，7 天一轮回。

**Mock 数据明细**（`weather` 每行 = `(天气状况, 最高温, 最低温)`，按 Day1…Day7 排列）：

- 北京：晴 22~28 / 多云 21~26 / 阴 20~25 / 晴 23~29 / 多云 22~27 / 晴 24~30 / 阴 21~26
- 上海：多云 24~30 / 小雨 23~28 / 晴 25~32 / 多云 24~29 / 晴 26~31 / 小雨 23~27 / 多云 25~30
- 杭州：晴 23~28 / 多云 22~27 / 小雨 21~26 / 晴 24~29 / 多云 23~28 / 晴 22~30 / 阴 21~27
- 成都：阴 20~25 / 多云 19~26 / 小雨 18~24 / 阴 20~27 / 多云 21~28 / 阴 19~25 / 小雨 18~23
- 广州：晴 27~33 / 雷阵雨 26~31 / 多云 27~34 / 晴 28~35 / 雷阵雨 25~30 / 多云 27~33 / 晴 26~34
- 深圳：多云 26~31 / 晴 27~33 / 雷阵雨 25~29 / 多云 26~32 / 晴 27~33 / 雷阵雨 24~28 / 多云 25~30
- 未收录城市：晴 20~28 / 多云 21~27 / 阴 19~25 / 晴 22~29 / 多云 20~26 / 晴 23~30 / 阴 19~25

`attractions` 景点池（组内按热度排序）：北京 `故宫/天安门/长城/颐和园/南锣鼓巷/798艺术区/天坛`、上海 `外滩/东方明珠/豫园/南京路/迪士尼/田子坊`、杭州 `西湖/灵隐寺/千岛湖/西溪湿地/宋城`、成都 `宽窄巷子/大熊猫基地/锦里/武侯祠/春熙路`、广州 `广州塔/沙面/陈家祠/白云山/长隆`、深圳 `世界之窗/欢乐谷/东部华侨城/莲花山`、未收录城市 `市中心广场/市政公园/当地博物馆`。

### 工具返回模型的字段规格

`src/models/tool_results.py` 中三个模型的字段由本节定义（该文件骨架阶段的「临时定义」警告至此解除）：

| 模型 | 字段 | 说明 |
| --- | --- | --- |
| `Attraction` | `name` / `city` / `ticket_price` / `rating` / `suggested_duration_min` | Mock 只提供 `name` 与 `city`；`ticket_price` / `rating` 为 `None`。v4.0 起 MCP 景点优先读高德 `biz_ext`，缺失时由名称确定性派生（见 [mcp.md](./mcp.md) §2.3）。票价留空**不影响** `cost` 工具 —— 其单价来自档位而非逐个景点累加。 |
| `RouteResult` | `city` / `ordered_spots` / `transport` / `total_travel_min` / `total_distance_km` | `ordered_spots` 与输入等长同序（消费方不得重排）。`transport`：≤2 个景点为「步行」，否则「地铁 + 步行」；`total_travel_min = (景点数 - 1) × 30`；`total_distance_km = (景点数 - 1) × 单跳距离`（步行 1.5km / 公共交通 5km）。 |
| `CostResult` | `days` / `tickets_total` / `transport_total` / `lodging_total` / `food_total` / `total` / `within_budget` | `total = days × 单价`，单价按预算反推档位：日预算 <600 取 300 元档，600–1200 取 600 元档，>1200 取 1200 元档，未给预算取 600 元档。v3.0 起拆分为**四段**：住宿 45% / 餐饮 25% / 市内交通 15% / 门票为余额，保证四项之和恰好等于 `total`。`within_budget` 在未提供 `budget` 时为 `None`。 |

## 1. 功能描述

统一管理外部工具的注册与调用，是 ReasoningEngine 获取外部信息的唯一通道。内置 `weather` / `attractions` / `route` / `cost` 四个工具，并支持后续注册新工具。本模块不决定「何时调用哪个工具」，那属于 ReasoningEngine 的职责。

## 2. 接口签名

```python
class ToolCenter:
    def __init__(self, mcp_client: AmapMCPClient | None = None) -> None: ...
    def register(self, name: str, func: Callable[..., Any]) -> None: ...
    async def call(self, name: str, **params: Any) -> Any: ...
    async def validate_destination(self, city: str) -> bool: ...
    async def close(self) -> None: ...
    def list_tools(self) -> list[str]: ...

TOOL_DEFINITIONS: list[dict[str, Any]]  # 4 个工具的 OpenAI Function Calling 描述
MOCK_CITY_WHITELIST: frozenset[str]    # 6 个 Mock 城市，即 ATTRACTIONS 的键
```

| 方法 | 签名 | 含义 |
| --- | --- | --- |
| `__init__` | `(mcp_client: AmapMCPClient \| None = None) -> None` | 初始化注册表并注册 4 个内置工具；`mcp_client=None` 时按环境变量构建 MCP 客户端，显式传入用于测试注入 |
| `register` | `(name: str, func: Callable[..., Any]) -> None` | 注册工具；同步 / 异步函数均可；重名抛 `ToolCallError` |
| `call` | `async (name: str, **params: Any) -> Any` | 按名调用并 `await`；同步工具直接执行，异步工具（含返回 awaitable 的同步函数）自动等待；工具不存在或执行失败抛 `ToolCallError` |
| `validate_destination` | `async (city: str) -> bool` | MCP 地理编码 → Mock 白名单；空串或均未命中返回 `False`，**不抛异常** |
| `close` | `async () -> None` | 关闭内部 MCP 客户端；未配置时为空操作，可重复调用 |
| `list_tools` | `() -> list[str]` | 返回已注册工具名的列表（副本） |

### 内置工具

| 工具名 | 签名 | 返回 | 数据来源 |
| --- | --- | --- | --- |
| `weather` | `async (city: str, days: int) -> list[WeatherInfo]` | 逐日天气 | 高德 MCP `maps_weather` → 静态表 |
| `attractions` | `async (city: str, limit: int = 10) -> list[Attraction]` | 景点列表 | 高德 MCP `maps_text_search` → 本地景点池 |
| `route` | `async (city: str, spots: list[str]) -> RouteResult` | 游览顺序、交通方式与总距离 | 高德 MCP 地理编码 + 驾车路线 → 本地跳数估算 |
| `cost` | `(days: int, spots: list[str], budget: float \| None = None) -> CostResult` | 四段花费估算 | 本地计算（不接 MCP） |

### Function Calling 工具描述（`TOOL_DEFINITIONS`）

4 个工具的 OpenAI 兼容描述，`name` 与注册表**严格一致**，ReasoningEngine 把
模型返回的 `tool_calls[].function.name` 直接交给 `ToolCenter.call()`，不做名称
映射。参数描述即工具签名的 JSON Schema 投影：

| 工具 | 必填参数 | 可选参数 |
| --- | --- | --- |
| `attractions` | `city` | `limit` |
| `weather` | `city` / `days` | — |
| `route` | `city` / `spots` | — |
| `cost` | `days` / `spots` | `budget`（可为 null） |

约束：`TOOL_DEFINITIONS` 与注册表的一致性由测试
`test_tool_definitions_match_registry` 守护；新增工具时必须同时登记两者。

### 关于 `**params` 的例外

`call(name, **params)` 会把参数展开为关键字实参，这与 CLAUDE.md「所有模块间通信使用 Pydantic 模型，禁止裸 dict 传递」存在张力。这是**经确认保留的例外**，适用边界如下：

- 仅限工具调用这一个入口，其他模块间通信一律仍用 Pydantic 模型
- 每个工具函数**必须**声明完整的参数与返回类型标注，调用方按签名传参
- 工具返回值必须是 Pydantic 模型或模型列表，**不得**返回裸 `dict`
- 不得以「工具调用可以」为由，把 `**kwargs` 扩散到 Scheduler / ReasoningEngine / Bridge 的接口

该例外已记录在 CLAUDE.md。

## 3. 外部依赖

| 依赖 | 用途 | 状态 |
| --- | --- | --- |
| 高德地图 MCP Server | `weather` / `attractions` / `route` 的真实数据源，经 JSON-RPC over HTTP 调用 | v4.0 起实际使用，见 [mcp.md](./mcp.md) |
| 环境变量 `AMAP_MCP_URL` | MCP 端点；未配置时三个工具全部走 Mock，不出网 | v4.0 起实际使用 |
| 环境变量 `AMAP_MCP_KEY` | 高德 API Key；URL 已含 `key` 时可留空 | v4.0 起实际使用 |
| `httpx` | **异步** HTTP 客户端（`httpx.AsyncClient`），握手与工具调用共用 | v4.0 起由 MCP 客户端使用 |
| `src/models` | `WeatherInfo` / `Attraction` / `RouteResult` / `CostResult` | 使用中 |

`cost` 为纯本地实现，无外部服务依赖；其余三个工具在未配置 `AMAP_MCP_URL` 时
亦不发起网络请求 —— 这保证了外部服务不可用时行程仍能生成（`ReasoningEngine`
对单个工具失败已有字段级降级）。

## 4. 异常处理

| 异常 | 抛出条件 | 处理方式 |
| --- | --- | --- |
| `ToolCallError` (2002) | `name` 未在注册表中 | 向上冒泡 |
| `ToolCallError` (2002) | `params` 与工具签名不匹配（缺参 / 多参 / 类型错误） | 向上冒泡，`__cause__` 存原始 `TypeError` |
| `ToolCallError` (2002) | 工具执行过程中抛出任何异常 | 包装后冒泡，`__cause__` 保留原始异常 |
| `ToolCallError` (2002) | MCP 调用超时或返回非 2xx | **v4.0 起不再冒泡**：`weather` / `attractions` / `route` 内部捕获并回落 Mock |
| `ToolCallError` (2002) | MCP 响应不符契约（JSON-RPC error / 字段缺失） | 同上，回落 Mock |
| `ToolCallError` (2002) | `register` 时 `name` 已存在 | 本模块直接抛出 |

**v4.0 的降级口径**：

- `weather` / `attractions` / `route` 的 MCP 失败在 ToolCenter 内被消化，记录
  WARNING 后返回 Mock 结果，调用方**不会**收到 2002；
- 未配置 MCP 时不产生任何异常，直接走 Mock；
- `validate_destination()` 不抛异常，MCP 故障时按「未命中」继续查白名单；
- 其余情形（未知工具、签名不匹配、Mock 实现自身报错）仍统一收敛为
  `ToolCallError`，原始异常存入 `__cause__`。

**调用方需自行判断失败是否致命** —— 按 [ReasoningEngine SDD](./reasoning.md)，单个工具失败不应导致整次行程生成失败。ToolCenter 对 MCP 通路已内置降级，但对 Mock 通路的失败仍如实抛出。
