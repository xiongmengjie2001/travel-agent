# 出发日期支持 SDD

> 文件：`src/reasoning.py`、`src/scheduler.py`、`api/routes.py`、`src/models/chat.py`、`src/models/intent.py`
> 关联：[reasoning.md](./reasoning.md)、[scheduler.md](./scheduler.md)、[bridge.md](./bridge.md)

## 0. 变更摘要（2026-10-21）

出发日期由「无此概念」升级为**全链路支持**：

1. `ChatRequest.start_date` / `Intent.start_date` 新增 `date | None` 字段；
2. `parse_intent(text, start_date=None)` 支持从文本解析日期，调用方传入值优先；
3. `Scheduler.process_input(text, start_date=None)` 透传日期；
4. `POST /api/chat` 把请求体中的 `start_date` 传给 Scheduler；
5. 模板方案的每日日期、天气报告均以出发日期为第 1 天对齐。

## 1. 功能描述

用户可在请求体或自然语言中指定出发日期。解析结果必须贯穿「意图 → 行程生成 → 每日日程 → 天气报告 → 前端卡片」全链路，保证同一次请求内所有日期使用同一时间轴。

未指定时默认**明天**出发，避免生成日期为今天却需要用户当天出发的不合理方案。

## 2. 接口签名

```python
# src/models/chat.py
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    start_date: date | None = None


# src/models/intent.py
class Intent(BaseModel):
    ...
    start_date: date | None = None


# src/reasoning.py
def ReasoningEngine.parse_intent(
    self, text: str, start_date: date | None = None
) -> Intent: ...


# src/scheduler.py
async def Scheduler.process_input(
    self, text: str, start_date: date | None = None
) -> AgentResult: ...


# api/routes.py
@router.post("/chat")
async def chat(payload: dict, scheduler: Scheduler = Depends(get_scheduler)):
    request = Bridge.parse_chat_request(payload)
    return await scheduler.process_input(request.message, request.start_date)
```

### 日期解析规则

优先级：**调用方传入的 `start_date` > 文本中的日期 > 默认明天**。

文本日期按以下顺序匹配，命中即停：

| 优先级 | 形式 | 示例 | 说明 |
| --- | --- | --- | --- |
| 1 | ISO 日期 | `2026-10-25`、`2026/10/25`、`2026.10.25`、`2026年10月25日` | 年 / 月 / 日分隔符兼容 `-` `.` `/` 与「年」「月」「日 / 号」 |
| 2 | 中文月日 | `10月25日`、`10月25号` | 年份取当前年；若该日期**早于今天**，顺延到明年 |
| 3 | 相对词 | `今天` / `今日` / `明天` / `明日` / `后天` / `大后天` | 以服务器本地日期为基准，偏移 0 / 1 / 2 / 3 天 |
| 4 | 未命中 | — | `date.today() + 1 天`（默认明天） |

非法月日（如 `2026-02-30`）不视为日期，继续尝试下一条规则，最终回落默认值。

### 预算提取的互斥处理

解析出「N 天」与日期片段后，先从文本中**挖空**这两个片段，再提取预算。否则：

- `2026年10月25日去北京玩3天，预算5000元` 的年份 `2026` 会被 `\d{3,5}` 误认成预算；
- `10月25日` 的 `25` 不会被误认（不足 3 位），但挖空规则对两者统一生效，避免后续出现 3 位以上的月日组合（如 `100月` 不可能，但保持规则一致）。

### 天气对齐

天气工具始终从**今天**起返回连续日期，而行程可能从未来某天开始。`_fetch_weather_aligned()` 的处理：

```
offset = max((start_date - today).days, 0)
fetch_days = min(行程天数 + offset, 30)      # 天气工具上限 30 天
report = await weather(city, fetch_days)
weather = report[offset : offset + 天数]
```

- 出发日期在未来 → 多取前置日期后切片，天气与每日日程逐日对齐；
- 出发日期在过去或今天 → `offset = 0`，直接取前 N 天；
- 出发日期超出 30 天预报窗口 → 超出部分天气留空，**不影响行程生成**。

### 每日日期生成

- 模板路径：`daily[i].date = start_date + i 天`；
- LLM 路径：`_parse_llm_plan` 忽略模型给出的日期，统一按 `start_date + i` 重排，防止模型篡改时间轴；
- `TripPlan.weather_report` 与 `daily` 使用同一日期序列。

## 3. 外部依赖

| 依赖 | 用途 |
| --- | --- |
| Pydantic v2 | `ChatRequest.start_date` 的 ISO 日期解析；非法格式转 `InvalidRequestError` |
| 标准库 `datetime.date` / `timedelta` | 日期运算，无第三方依赖 |
| `ToolCenter.call("weather", ...)` | 按「今天起」语义返回逐日天气，由本模块负责对齐切片 |

## 4. 异常处理

| 场景 | 异常 / 行为 | 对外 code |
| --- | --- | --- |
| 请求体 `start_date` 非日期字符串 | `Bridge.parse_chat_request` 捕获 `ValidationError` → `InvalidRequestError` | 1001 / HTTP 400 |
| 文本日期非法（如 2 月 30 日） | 不抛异常，继续下一条规则，最终默认明天 | — |
| 日期超出天气工具 30 天窗口 | 不抛异常，超出的每日天气为 `None` | — |
| `start_date` 为 `null` / 缺省 | 走文本解析或默认明天 | — |

日期解析失败**绝不**导致行程生成失败：日期是增强字段，不是生成的前置条件。

## 5. 验收清单

- [ ] `POST /api/chat` 带 `start_date` 时，响应中 `trip.daily[0].date` 等于该日期，后续日期逐日递增（`test_routes.py::test_chat_accepts_start_date`）
- [ ] 文本 `10月25日出发` 能被解析，且预算不被月 / 日污染（`test_reasoning.py`）
- [ ] `今天` / `明天` / `后天` / `大后天` 分别偏移 0 / 1 / 2 / 3 天
- [ ] 未指定日期时默认明天
- [ ] 调用方传入的 `start_date` 覆盖文本日期
- [ ] `weather_report[i].date` 与 `daily[i].date` 一致（未来出发日期）
- [ ] `start_date` 非法字符串返回 1001 / HTTP 400
- [ ] 全量测试 `python -m pytest tests/ -q` 通过
