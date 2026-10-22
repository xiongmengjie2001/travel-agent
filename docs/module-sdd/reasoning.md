# ReasoningEngine SDD

> 文件：`src/reasoning.py`

## 0. v3.0 变更（2026-10-21）：Function Calling 多轮工具调用 + 全链路异步

AI 路径由「单次提示词生成 JSON」升级为 **Function Calling 多轮循环**：

1. `generate_itinerary` / `generate_template` 及全部工具适配方法改为 `async`；
2. 首次请求即携带 `TOOL_DEFINITIONS`（4 个工具的 OpenAI 格式描述），由模型
   自主决定调用景点 / 天气 / 路线 / 花费工具；
3. 每轮把模型返回的 `tool_calls` 逐一执行，工具结果以 `role=tool` 消息回传，
   模型据此继续推理；**最多 3 轮**（`MAX_TOOL_ROUNDS`）；
4. 模型不再发起工具调用时，解析最终 JSON；若缺少天气 / 花费增强字段，自动
   调用工具补全（`_enrich_plan`）；
5. `_call_deepseek` 支持 `tools` 参数，超时保持 30s；返回 assistant message
   对象（而非纯文本），以承载 `tool_calls`；
6. 新增 `AI_SYSTEM_PROMPT`：要求先调工具、禁止编造景点 / 天气、最终只输出
   标准 JSON。

模板路径与降级规则不变：AI 路径失败或轮数耗尽时仍回落到模板方案（AC-5）。

## 0.1 v4.0 变更（2026-10-21）：日期解析、未知城市提取与目的地校验

- `parse_intent(text, start_date=None)` 新增出发日期参数与文本日期解析
  （规则见 [date-support.md](./date-support.md)）；
- 目的地提取在预设城市之外，支持「去 / 到 / 前往 + 地名」与「地名 + N 天 /
  日」两种句式（如「西安4日游」「天津3天」）；
- 新增 `validate_destination(city)`，`generate_itinerary()` 在生成前校验，
  无效时抛 `DestinationInvalidError`（见 [destination-validation.md](./destination-validation.md)）；
- 模板方案与 AI 补全统一填充 `weather_report` / `cost_breakdown` / `source`；
- 天气按出发日期对齐（`_fetch_weather_aligned`），保证日程与天气同一时间轴。

## 1. 功能描述

把用户自然语言需求解析为结构化意图，并据此生成完整行程方案。优先通过 DeepSeek API 完成，调用失败时降级为本地模板方案，保证接口始终可用。本模块不读写数据库，也不做 API 格式转换。

## 2. 接口签名

```python
class ReasoningEngine:
    def __init__(self, tool_center: ToolCenter, api_key: str | None = None) -> None: ...
    def parse_intent(self, text: str, start_date: date | None = None) -> Intent: ...
    async def validate_destination(self, city: str) -> bool: ...
    async def generate_itinerary(self, intent: Intent) -> TripPlan: ...
    async def generate_template(self, intent: Intent) -> TripPlan: ...
    async def _generate_with_tools(self, intent: Intent) -> TripPlan | None: ...
    async def _run_tool_call(self, call: dict[str, Any]) -> dict[str, Any]: ...
    async def _enrich_plan(self, plan: TripPlan, intent: Intent) -> TripPlan: ...
    async def _call_deepseek(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None: ...
    def _build_messages(self, intent: Intent) -> list[dict[str, Any]]: ...
    def _build_prompt(self, intent: Intent) -> str: ...
```

| 方法 | 签名 | 可见性 | 含义 |
| --- | --- | --- | --- |
| `__init__` | `(tool_center: ToolCenter, api_key: str \| None = None) -> None` | public | 注入工具中心。`api_key=None` 时从 `DEEPSEEK_API_KEY` 读取；仍为空则进入纯模板模式 |
| `parse_intent` | `(text: str, start_date: date \| None = None) -> Intent` | public | 解析目的地 / 天数 / 出发日期 / 预算 / 偏好（纯本地，无 IO） |
| `validate_destination` | `async (city: str) -> bool` | public | 委托 `ToolCenter.validate_destination`；替身未实现时视为通过 |
| `generate_itinerary` | `async (intent: Intent) -> TripPlan` | public | 生成行程，**内部完成全部降级**；目的地无效时抛 `DestinationInvalidError` |
| `generate_template` | `async (intent: Intent) -> TripPlan` | public | 本地模板方案，恒定标记 `degraded=True` |
| `_generate_with_tools` | `async (intent: Intent) -> TripPlan \| None` | private | Function Calling 多轮循环；失败 / 轮数耗尽返回 `None` |
| `_run_tool_call` | `async (call: dict[str, Any]) -> dict[str, Any]` | private | 执行单个 `tool_call`，返回 `role=tool` 消息；错误作为结果回传模型 |
| `_enrich_plan` | `async (plan: TripPlan, intent: Intent) -> TripPlan` | private | AI 行程缺天气 / 花费时调工具补齐 |
| `_call_deepseek` | `async (messages, tools=None) -> dict[str, Any] \| None` | private | 发起一次 DeepSeek 调用，返回 assistant message；**失败返回 `None`** |
| `_build_messages` | `(intent: Intent) -> list[dict[str, Any]]` | private | 组装 system + user 初始消息 |
| `_build_prompt` | `(intent: Intent) -> str` | private | 用户需求文本（输出 JSON 的约束在系统提示词中） |

### 目的地提取规则

按以下顺序尝试，命中即停：

1. **预设城市**：在文本中查找 `KNOWN_CITIES`（北京 / 上海 / 杭州 / 成都 / 广州 / 深圳），
   取出现位置最靠前者；
2. **动词句式**：`(?:去|到|前往|想去|要去)\s*([一-龥]{2,4})`；
3. **地名 + 天数**：`([一-龥]{2,4})\s*(?:\d+|[一二三四五六七八九十两]+)\s*(?:天|日)`，
   支持「西安4日游」「三亚三天」。

候选词随后清洗：剥离「帮我 / 规划 / 去 / 到」等前缀；第 3 个字起遇到
「天 / 日 / 游 / 玩 / 的 / 旅行 …」等噪声字即截断（前两个字不截断，避免
「天津」「天山」被误伤）；清洗后长度不足 2 判为失败。仍无候选则抛
`IntentUnrecognizedError`。

### 出发日期解析

优先级：**调用方传入的 `start_date` > 文本日期 > 明天**。完整规则、预算挖空
与天气对齐见 [date-support.md](./date-support.md)。

> **`_call_deepseek` 返回值说明**
>
> v3.0 起返回 `choices[0].message`（dict），而不是纯文本：Function Calling
> 需要同时承载 `content` 与 `tool_calls`。失败仍以 `None` 表达，由
> `generate_itinerary` 统一决定降级。

### 关键常量

```python
DEEPSEEK_BASE_URL = "https://api.deepseek.com"   # OpenAI 兼容接口
DEFAULT_MODEL = "deepseek-v4-flash"
ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_USE_MOCK_LLM = "USE_MOCK_LLM"
DEFAULT_HTTP_TIMEOUT = 30.0
MAX_TOOL_ROUNDS = 3
AI_SYSTEM_PROMPT = "..."
```

`base_url` 与 `model` 允许通过环境变量 `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 覆盖，默认值如上。**API Key 只从环境变量读取，禁止硬编码或写入配置文件**（CLAUDE.md 禁止事项）。

**环境变量的完整清单、加载方式与优先级以 [config.md](./config.md) 为准**，本文件只描述 `ReasoningEngine` 如何消费它们。

`USE_MOCK_LLM` 为真时构造出的引擎**强制不出网**：`_call_deepseek` 在检查 `api_key` **之前**先行返回 `None`，因此配了 Key 也照样走模板降级。这是刻意的顺序 —— 设了 Key 又开了 Mock，表达的是「不想调用」，不该被 Key 的存在推翻。判定规则见 config.md §2.2，取值在**构造期**读取一次，运行中改环境变量不生效。

### Function Calling 流程

```
generate_itinerary(intent)
  └─ _generate_with_tools(intent)
       messages = [system(AI_SYSTEM_PROMPT), user(需求)]
       for round in 1..MAX_TOOL_ROUNDS:
           message = _call_deepseek(messages, tools=TOOL_DEFINITIONS)
           if message is None: return None            # 网络 / 解析失败 → 降级
           messages += assistant(message)
           if message.tool_calls 为空:
               plan = _parse_llm_plan(message.content, intent)
               return await _enrich_plan(plan, intent)   # 缺字段补全
           for call in message.tool_calls:
               messages += await _run_tool_call(call)     # role=tool 结果
       return None                                   # 轮数耗尽 → 降级
  └─ generate_template(intent)                        # degraded=True
```

约定：

- `tool_calls` 的 `function.name` 必须命中 `ToolCenter` 注册表，否则错误文本
  回传模型；
- 工具参数按 JSON 解析后 `**` 展开传给 `ToolCenter.call()`；
- 工具结果必须序列化为 JSON 文本（Pydantic 模型走 `model_dump(mode="json")`）；
- 单个工具失败**不中断整轮推理**：错误以 `{"error": "..."}` 回传，模型可自行
  修正（例如减少天数、更换城市）；
- 模型同时返回 `content` 与 `tool_calls` 时，以 `tool_calls` 为准继续循环。

### 增强字段补全（`_enrich_plan`）

`_parse_llm_plan` 负责结构校验与天数截断；`_enrich_plan` 只补「模型没给」的
增强字段，不改写有效数据：

| 缺失情形 | 补全方式 |
| --- | --- |
| 任一日 `weather` 为 `None` | 调 `weather(destination, days)`，按日序回填 |
| `weather_report` 为空 | 从填好的 `daily[].weather` 汇总 |
| `total_cost <= 0` | 调 `cost(days, spots, budget)`，总价与空白日花费一并回填 |
| `cost_breakdown` 为 `None` | 复用（或新调）`cost` 工具结果补齐四段分项 |
| `source` 为空或 `"unknown"` | 置为 `"llm"` |

补全后重建 `TripPlan` 校验失败时，**保留模型原始产出**并记 WARNING —— 补全
属于增强项，不应把一份合法行程拖成降级方案。

### 降级规则

| 级别 | 触发条件 | 行为 | `degraded` |
| --- | --- | --- | --- |
| 1 正常 | 多轮循环内模型输出可解析为 `TripPlan` | 返回 LLM 产出（可经补全） | `False` |
| 2 模板模式 | `api_key` 为空**或** `USE_MOCK_LLM` 为真 | `await generate_template(intent)` | `True` |
| 3 运行时降级 | 超时 / 网络错误 / HTTP 非 2xx / 响应不可解析 / 轮数耗尽 | 捕获后 `await generate_template(intent)` | `True` |
| 4 兜底 | 模板方案本身失败（理论上不应发生） | 抛 `LLMUnavailableError` | — |

**级别 3 是必须实现路径，不是可选项** —— PRD AC-5 要求断开 API Key 后仍返回结构完整的行程。

`generate_template` 的产出必须独立满足 AC-1 的结构要求：每日景点列表、路线顺序、预估花费、天气信息。天气仍尽力经 `tool_center.call("weather", ...)` 获取。

### 工具失败的粒度

| 工具 | 失败后果 |
| --- | --- |
| `weather` | `DailyPlan.weather = None`，行程照常生成 |
| `attractions` | 回退到模板内置景点池 |
| `route` / `cost` | 按已有数据本地估算 |

AI 路径下工具失败会先回传模型，由模型决定是否重试；模板路径按上表降级。
工具失败**不得**让整次生成失败。仅当 LLM 与模板**同时**不可用时才抛异常。

## 3. 外部依赖

| 依赖 | 用途 |
| --- | --- |
| DeepSeek API | OpenAI 兼容接口，`base_url=https://api.deepseek.com`，默认模型 `deepseek-v4-flash` |
| 环境变量 `DEEPSEEK_API_KEY` | API Key 唯一来源 |
| 环境变量 `USE_MOCK_LLM` | 为真时强制不出网（见 config.md §2.3） |
| `httpx` | 异步 HTTP 客户端，超时 30s（Function Calling 多轮调用共用） |
| `ToolCenter` | `await call("weather" / "attractions" / "route" / "cost")`，构造函数注入 |
| `TOOL_DEFINITIONS` | 4 个工具的 OpenAI Function Calling 描述（tools.md §2） |
| `src/models` | `Intent` / `TripPlan` / `DailyPlan` / `Spot` / `WeatherInfo` |

DeepSeek 为**直连**，不经由 ToolCenter —— ToolCenter 管理的是可注册的业务工具，LLM 推理通路不属于工具调用语义。

## 4. 异常处理

| 异常 | 抛出条件 | 处理方式 |
| --- | --- | --- |
| `IntentUnrecognizedError` (1002) | `parse_intent` / `generate_itinerary` 无法识别目的地 | 向上冒泡 |
| `DestinationInvalidError` (1003) | 目的地未通过 `validate_destination()` 校验 | 向上冒泡；正常路径由 Scheduler 转为对话引导 |
| `LLMUnavailableError` (2001) | DeepSeek 失败**且** `generate_template` 也失败 | 向上冒泡 |
| `ToolCallError` (2002) | 模板路径中工具失败且该工具为必需（当前无必需工具） | 向上冒泡 |

**不抛异常的场景**（务必与上表区分）：

- DeepSeek 单点失败但模板可用 → 返回 `degraded=True` 的正常 `TripPlan`
- Function Calling 中单个工具失败 → 错误回传模型，继续循环
- 单个工具失败但行程仍可生成 → 返回结果，相应字段置空或走估算
- `USE_MOCK_LLM` 为真 → 静默走模板降级，`degraded=True`，**不打 ERROR 也不抛异常**（这是被要求的正常路径，不是故障）

`USE_MOCK_LLM` 的日志口径：只打一条 INFO（「跳过真实 LLM 调用」），**不发 WARNING**。
`generate_itinerary` 中那条「DeepSeek 不可用或响应无法解析」以 `not self._mock_llm`
为前置 —— 否则用户主动选择的离线模式会被报成故障，把排查引向不存在的网络问题。

注意 `generate_itinerary` 的 LLM 通路以 `if self._api_key:` 开头，因此**未配 Key 时
根本不会调用 `_call_deepseek`**，Mock 那条 INFO 也就不会出现（此时「不出网」已由
空 Key 保证，无须开关介入）。想看到该 INFO，需同时配 Key 与开关。

`_call_deepseek` 内部捕获 `httpx.TimeoutException` / `httpx.HTTPError` / JSON 解析异常，**不向外抛出** —— 由 `generate_itinerary` 统一决定是否降级。
