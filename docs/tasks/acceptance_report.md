# SDD验收报告

> 核对日期：2026-10-03（v2.0 复核同日）
> 核对范围：`docs/module-sdd/` 四份 SDD 的 **§2 接口签名**（含 §2 内的常量 / 返回值模型 / 内置工具表），逐条对照 `src/` 实现
> 不在本报告范围：各 SDD 的 §3 外部依赖、§4 异常处理、§5 并发、§7 验收清单未逐条核对
> 状态口径：`pass` = 签名与实现逐字一致；`partial` = 签名一致但实现内涵与 SDD 声明不符；`fail` = 缺失或签名不符

## 核对方法

```bash
# 1) 抽出 SDD §2 的全部签名与常量
for f in docs/module-sdd/{scheduler,reasoning,tools,bridge}.md; do
  echo "## $f"; sed -n '/^## 2\./,/^## 3\./p' "$f"
done

# 2) 抽出代码的全部签名与常量（含行号）
for f in src/{scheduler,reasoning,tools,bridge}.py; do
  echo "## $f"; grep -n "^ACTION_\|^    def \|^def \|^class \|^DEEPSEEK_BASE_URL\|^DEFAULT_MODEL\|^ENV_API_KEY\|^ENV_BASE_URL\|^ENV_MODEL\|^DEFAULT_HTTP_TIMEOUT" "$f"
done

# 3) 行为一致性由测试覆盖
pytest tests/ -v
```

⚠️ **重要披露**：核对过程中发现 `reasoning.md` 与 `bridge.md` 的 §2 签名与代码不一致（各 1 处），已将 **SDD 回写对齐代码**（记为 v1.0 → v1.1，改动理由写在各自 §2 的说明块内）。因此本报告中标 `pass` 的这两行，含义是「现已一致」，**不是「代码一开始就符合 SDD」**。同类修正还发生在 `memory.md`（不在本次四份文件范围内）：`save_trip` 补充 `intent_raw` 参数。

⚠️ **与上述相反的一处披露**：`weather` 的 partial 是**改代码**消除的（v2.0 实现 MCP 数据源），不是回写 SDD —— 该行标 `pass` 的含义是「代码确实符合 §2 原文的数据源声明」，无需任何口径让步。

---

## scheduler.md

对照 `src/scheduler.py`。

| SDD条目 | 代码位置 | 状态 | 备注 |
|---------|---------|------|------|
| `__init__(self, reasoning: ReasoningEngine, memory: MemoryStore) -> None` | src/scheduler.py:77 | pass | 仅赋值 `_reasoning` / `_memory`，未自行实例化下游（SDD 明确要求） |
| `process_input(self, text: str) -> AgentResult` | src/scheduler.py:87 | pass | Observe→Orient 日志 + `Bridge.detect_action` + 转 `_execute_action` |
| `_execute_action(self, action: str, text: str) -> AgentResult` | src/scheduler.py:103 | pass | 5 个分支与 SDD 动作分派表一一对应；兜底 `ACTION_CHAT` |
| `_handle_plan_trip(self, text: str) -> AgentResult` | src/scheduler.py:115 | pass | `parse_intent` → `generate_itinerary` → `save_trip`，先保存再返回（AC-2） |
| `_handle_list_trips(self, text: str) -> AgentResult` | src/scheduler.py:138 | pass | 直连 `memory.list_trips()` |
| `_handle_delete_trip(self, text: str) -> AgentResult` | src/scheduler.py:146 | pass | 比 SDD 多一道守卫：文本中提不出 trip_id 时抛 `InvalidRequestError`（SDD §4 已规定） |
| `_handle_search_trips(self, text: str) -> AgentResult` | src/scheduler.py:162 | pass | 关键词经 `_extract_keyword` 剥离「搜索 / 找一下」等填充词 |
| `ACTION_PLAN_TRIP = "plan_trip"` | src/scheduler.py:25 | pass | 字面值一致 |
| `ACTION_LIST_TRIPS = "list_trips"` | src/scheduler.py:26 | pass | 字面值一致 |
| `ACTION_SEARCH_TRIPS = "search_trips"` | src/scheduler.py:27 | pass | 字面值一致 |
| `ACTION_DELETE_TRIP = "delete_trip"` | src/scheduler.py:28 | pass | 字面值一致 |
| `ACTION_CHAT = "chat"` | src/scheduler.py:29 | pass | 字面值一致 |
| `class AgentResult: action / message / trip / trips` | src/scheduler.py:58 | pass | 字段与默认值一致；`trips` 用 `default_factory=list` 实现 SDD 的 `= []`（避免可变默认值共享同一列表） |

## reasoning.md

对照 `src/reasoning.py`。

| SDD条目 | 代码位置 | 状态 | 备注 |
|---------|---------|------|------|
| `__init__(self, tool_center: ToolCenter, api_key: str \| None = None) -> None` | src/reasoning.py:113 | pass | 行为符合：`api_key=None` 时读 `DEEPSEEK_API_KEY`；Key 只从环境变量来，无硬编码 |
| `parse_intent(self, text: str) -> Intent` | src/reasoning.py:130 | pass | 与课程参考版的关键差异：无目的地时**抛 `IntentUnrecognizedError`** 而非返回 `None`（1002 靠异常表达）；天数先抽取并遮罩区间后再找预算，参考版的 `(\d{3,5})` 会误取天数 |
| `generate_itinerary(self, intent: Intent) -> TripPlan` | src/reasoning.py:187 | pass | 内部完成全部降级，失败不抛异常；仅 LLM 与模板同时不可用才抛 `LLMUnavailableError` |
| `_call_deepseek(self, prompt: str) -> str \| None` | src/reasoning.py:310 | pass | **SDD §2 原为 `-> str`，本次核对时修正为 `str \| None`** —— SDD §4 要求该方法不向外抛，失败只能以空值表达 |
| `generate_template(self, intent: Intent) -> TripPlan` | src/reasoning.py:215 | pass | 恒定 `degraded=True`；工具失败逐项降级（天气置 None、景点回落素材池、花费本地估算） |
| `DEEPSEEK_BASE_URL = "https://api.deepseek.com"` | src/reasoning.py:50 | pass | 字面值一致 |
| `DEFAULT_MODEL = "deepseek-v4-flash"` | src/reasoning.py:51 | pass | 字面值一致 |
| `ENV_API_KEY = "DEEPSEEK_API_KEY"` | src/reasoning.py:52 | pass | 字面值一致 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 可被环境变量覆盖 | src/reasoning.py:53-54, 124-125 | pass | `ENV_BASE_URL` / `ENV_MODEL` 经 `os.getenv(..., 默认值)` 读取，并 `rstrip("/")` |
| HTTP 超时（SDD §3「建议 30s」） | src/reasoning.py:55 | pass | `DEFAULT_HTTP_TIMEOUT = 30.0`，与 §3 一致（未采用课程实现说明的 10s） |

## tools.md

对照 `src/tools.py`。

| SDD条目 | 代码位置 | 状态 | 备注 |
|---------|---------|------|------|
| `__init__(self) -> None` | src/tools.py:150 | pass | 自动注册四个内置工具，注册名与 §2 内置工具表逐字一致 |
| `register(self, name: str, func: Callable[..., Any]) -> None` | src/tools.py:158 | pass | 重名抛 `ToolCallError` |
| `call(self, name: str, **params: Any) -> Any` | src/tools.py:172 | pass | 未注册 / 参数不匹配 / 执行失败三路均转 `ToolCallError`；`AgentError` 原样冒泡不二次包装 |
| `list_tools(self) -> list[str]` | src/tools.py:200 | pass | 返回 `list(self._tools)` 副本，调用方改动不影响注册表 |
| `weather(city: str, days: int) -> list[WeatherInfo]` | src/tools.py:205 | pass | 签名一致；**数据源已于 v2.0 对齐** —— 设了 `MCP_WEATHER_URL` 走 MCP 天气服务（HTTP，契约见 tools.md §0），未设则回落 `WEATHER_PATTERN` 静态表作为本地降级。`httpx` / `MCP_WEATHER_URL` 已成为实际依赖，§2 声明的数据源成立 |
| `attractions(city: str, limit: int = 10) -> list[Attraction]` | src/tools.py:311 | pass | §2 数据源为「MCP / 本地景点池」，当前取本地 `ATTRACTIONS` 池，落在声明范围内 |
| `route(city: str, spots: list[str]) -> RouteResult` | src/tools.py:341 | pass | 本地计算，与 §2 一致；保持输入顺序，跳数 × 30 分钟 |
| `cost(days: int, spots: list[str], budget: float \| None = None) -> CostResult` | src/tools.py:369 | pass | 本地计算，与 §2 一致；三档单价 300/600/1200，住宿/餐饮/门票三段合计等于总额 |

> `weather` 原标 `partial` 的理由：签名层已 pass，但 §2 该行同时声明了数据来源，而当时实现是纯 Mock。**该 partial 已于 v2.0 消除**：`_weather` 改为双数据源（`MCP_WEATHER_URL` 配置则走 HTTP，未配置回落静态表），tools.md §0 已登记对接契约与切换语义，`httpx` / `MCP_WEATHER_URL` 转为实际依赖。新增 5 组 MCP 通路用例（成功 / 传参 / HTTP 失败 / 5 类畸形响应）。

## bridge.md

对照 `src/bridge.py`。

| SDD条目 | 代码位置 | 状态 | 备注 |
|---------|---------|------|------|
| `parse_chat_request(payload: dict) -> ChatRequest` | src/bridge.py:55 | pass | 非 dict / 缺 message / 校验失败三路均转 `InvalidRequestError`（否则会落到兜底 5000） |
| `to_api_response(data: Any = None, error: Exception \| None = None) -> dict` | src/bridge.py:81 | pass | 永不抛异常；`AgentError` 按自身 code，其他归 5000 |
| `to_trip_card(plan: TripPlan, trip_id: str = "") -> TripCard` | src/bridge.py:105 | pass | **SDD §2 原为 `(plan: TripPlan)`，本次核对时补充 `trip_id`** —— 该字段由落库时才生成，`TripPlan` 不含它，而它是前端删除的句柄（AC-2） |
| `detect_action(text: str) -> str` | src/bridge.py:125 | pass | 删除 > 查看 > 检索 > 规划 的优先级与 §2 表一致；无失败路径 |
| `ACTION_PLAN_TRIP = "plan_trip"` | src/bridge.py:35 | pass | 与 scheduler.md 同名常量字面值一致 |
| `ACTION_LIST_TRIPS = "list_trips"` | src/bridge.py:36 | pass | 同上 |
| `ACTION_SEARCH_TRIPS = "search_trips"` | src/bridge.py:37 | pass | 同上 |
| `ACTION_DELETE_TRIP = "delete_trip"` | src/bridge.py:38 | pass | 同上 |
| `ACTION_CHAT = "chat"` | src/bridge.py:39 | pass | 同上 |

---

## 签名之外：SDD §2 附带的行为声明核对

这些不是签名，但写在 §2 内且可验证，一并核对。

| 模块 | SDD 声明 | 状态 | 依据 |
| --- | --- | --- | --- |
| scheduler | 动作分派表 5 行（plan→三连调用、list、search、delete、chat 不碰下游） | pass | `_execute_action` 五分支；`tests/test_scheduler.py` 覆盖 |
| scheduler | `trip` 与 `trips` 至多一个非空 | pass | 各 handler 只填其一；路由测试断言 plan 分支 `trips == []` |
| reasoning | 降级规则 4 级（正常 / 无 Key 走模板 / 运行时失败走模板 / 双失败抛 2001） | pass | 4 级均有对应用例；降级不占错误码，`code` 仍为 0（AC-5） |
| reasoning | 标准库外的 HTTP 客户端为 `httpx`（§3） | pass | 代码 `import httpx`；课程实现说明中的 `openai` SDK 未采用（不在依赖内） |
| bridge | `to_api_response` 语义表 5 行 | pass | 5 行各有对应用例（含 `degraded` 仍为 code 0） |
| bridge | 动作识别优先级表 5 行 | pass | `test_detect_action` 参数化 13 例，含「删除北京行程」优先级验证 |
| tools | `**params` 例外边界 4 条 | pass | `call` 是唯一展开传参入口；4 个工具均带完整标注且返回 Pydantic 模型 |
| tools | §3 声明 `httpx` / `MCP_WEATHER_URL` 为依赖 | pass | v2.0 起两者均为实际依赖（`src/tools.py:23` 导入 `httpx`、`src/tools.py:143-144` 定义 `ENV_MCP_WEATHER_URL` / `MCP_WEATHER_TIMEOUT`） |

---

## 总结

- 总条目数: 40
- pass: 40
- partial: 0
- fail: 0
- 通过率: **100%**

（2026-10-03 复核：v2.0 实现 `weather` 的 MCP 数据源后，原唯一的 partial 消除，
计数由 39/1/0 更新为 40/0/0。）

### 结论

四份 SDD 的 §2 接口签名与代码**逐条可对应，无缺失、无签名不符、无 partial**。
`weather` 的数据源差异已于 v2.0 消除（见 §0 与上表说明）。

### 遗留与建议

1. ~~`weather` 的 MCP 接入（第 20 节）~~ **已完成（v2.0）**：`_weather` 双数据源，tools.md §3 的 `httpx` / `MCP_WEATHER_URL` 已从「目标依赖」转为「实际依赖」。剩余的第 20 节工作仅 `attractions` 接入 MCP 景点源（该工具的 §2 数据源本就声明为「MCP / 本地景点池」，不构成差异）。
2. 本报告中 reasoning.md / bridge.md 的两处 `partial` 风险已通过**回写 SDD** 消除，但回写是「以代码为准」。若课程要求以 SDD 原文为准，需改为反向调整代码 —— 需确认，因为 `to_trip_card` 去掉 `trip_id` 会使 AC-2 断链，`_call_deepseek -> str` 无法表达失败。
3. `docs/sdd/memory-store.md` 与 `docs/module-sdd/memory.md` 为同一份规格的两个副本，易漂移；建议合并，尚未处理。
4. 本报告未覆盖 §3–§7，如需全量验收需另出报告。
