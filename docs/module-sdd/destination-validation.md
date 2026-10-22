# 目的地有效性校验 SDD

> 文件：`src/tools.py`（`ToolCenter.validate_destination`）、`src/reasoning.py`（`ReasoningEngine.validate_destination` / `generate_itinerary`）、`src/scheduler.py`（`_handle_plan_trip`）、`src/exceptions.py`
> 关联：[tools.md](./tools.md)、[mcp.md](./mcp.md)、[reasoning.md](./reasoning.md)、[scheduler.md](./scheduler.md)

## 0. 变更摘要（2026-10-21）

新增「目的地有效性校验」：

1. `ToolCenter.validate_destination(city)`：MCP 地理编码 → Mock 城市白名单 → 判无效；
2. `ReasoningEngine.generate_itinerary()` 在生成前校验，无效时抛 `DestinationInvalidError`；
3. `Scheduler._handle_plan_trip()` 捕获该异常，转为**一般对话引导文案**，不生成、不落库；
4. 新增错误码 `1003`（HTTP 200）。

## 1. 功能描述

在调用 LLM 或模板生成行程之前，先确认用户给出的目的地是真实存在的城市。目的是拦住「去火星玩 3 天」这类输入，避免：

- 调用 LLM / MCP 产生无意义的网络开销；
- 把一张内容荒诞的行程落库，污染历史记录；
- 前端拿到看似正常、实际不可用的方案。

校验采用**分层降级**：优先用高德 MCP 地理编码（能识别全国任意真实地名），MCP 不可用或未配置时回落到内置城市白名单，两者都未命中才判无效。

## 2. 接口签名

```python
# src/tools.py
class ToolCenter:
    async def validate_destination(self, city: str) -> bool: ...


# src/reasoning.py
class ReasoningEngine:
    async def validate_destination(self, city: str) -> bool: ...
    async def generate_itinerary(self, intent: Intent) -> TripPlan: ...


# src/exceptions.py
class DestinationInvalidError(AgentError):
    code: int = 1003
    http_status: int = 200
```

| 方法 | 签名 | 说明 |
| --- | --- | --- |
| `ToolCenter.validate_destination` | `async (city: str) -> bool` | **不抛异常**；空串 / 纯空白直接 `False` |
| `ReasoningEngine.validate_destination` | `async (city: str) -> bool` | 委托 `ToolCenter`；替身未实现该方法时视为通过 |
| `generate_itinerary` | `async (intent: Intent) -> TripPlan` | 校验失败抛 `DestinationInvalidError`，发生在任何生成动作之前 |

### 校验顺序

```
validate_destination(city)
  ├─ city.strip() 为空 ───────────────────────────────► False
  ├─ 已配置 AmapMCPClient
  │    └─ await city_exists(city) ── True ────────────► True
  │           └─ 异常 / False ──► 继续下一步
  └─ city ∈ MOCK_CITY_WHITELIST ── True / False ──────► 对应结果
```

| 步骤 | 条件 | 结果 |
| --- | --- | --- |
| 1 | 空字符串 / 纯空白 | `False` |
| 2 | MCP 已配置且地理编码命中 | `True` |
| 3 | MCP 未配置、未命中或调用异常 | 查 `MOCK_CITY_WHITELIST`（北京 / 上海 / 杭州 / 成都 / 广州 / 深圳） |
| 4 | 白名单也未命中 | `False` |

**关键设计**：MCP 命中即视为有效，即使城市不在 Mock 白名单内（如「西安」）。Mock 白名单只作为 MCP 不可用时的兜底，不是「允许清单」的完整集合。

`AmapMCPClient.city_exists()` 内部已捕获全部异常并返回 `False`；`ToolCenter` 仍额外包一层 `try/except`，用于兼容测试替身或未来实现变更 —— 校验失败必须回落，绝不能因校验本身出错而阻断规划。

### 无效目的地的处理

```
Scheduler._handle_plan_trip(text, start_date)
  ├─ intent = reasoning.parse_intent(...)
  ├─ plan = await reasoning.generate_itinerary(intent)
  │     └─ DestinationInvalidError
  │           └─ return AgentResult(action="chat", message=引导文案)
  └─ record = memory.save_trip(plan, ...)
```

- 返回的 `action` 为 `"chat"`（与一般对话共用动作值），`trip` 为 `None`；
- **不调用** `memory.save_trip()`，历史记录不增加；
- 响应 `code` 仍为 `0` —— 这不是服务故障，而是可预期的业务分支；
- 引导文案包含用户输入的目的地，便于其确认是否拼写错误。

## 3. 外部依赖

| 依赖 | 用途 |
| --- | --- |
| `AmapMCPClient.city_exists()` | MCP 地理编码校验；未配置时为 `None` |
| `MOCK_CITY_WHITELIST` | Mock 兜底白名单（来自 `ATTRACTIONS` 的城市键） |
| 环境变量 `AMAP_MCP_URL` / `AMAP_MCP_KEY` | 决定是否启用 MCP 校验；未设置时纯 Mock |

## 4. 异常处理

| 场景 | 异常 / 行为 | 对外表现 |
| --- | --- | --- |
| 目的地为空 | `ToolCenter.validate_destination` 返回 `False` | 由调用方决定；`parse_intent` 通常会先抛 1002 |
| 目的地不在白名单且 MCP 未命中 | `DestinationInvalidError` | `code=1003`，HTTP 200 |
| MCP 调用异常 | 捕获，回落白名单 | 不抛异常 |
| MCP 未配置 | 直接查白名单 | 不抛异常 |

`DestinationInvalidError` 是 `AgentError` 子类，若未被 Scheduler 捕获而冒泡到 `api/` 层，统一 handler 会转换为 `{"code": 1003, "msg": ..., "data": null}` / HTTP 200。当前正常路径由 Scheduler 转成对话引导，不会冒泡。

## 5. 行为对比

| 输入 | 校验前 | 校验后 |
| --- | --- | --- |
| `帮我规划去北京玩3天` | 生成行程并落库 | 不变（北京在白名单） |
| `帮我规划去西安玩3天`（未配 MCP） | 生成模板行程并落库 | 返回引导文案，不落库 |
| `帮我规划去西安玩3天`（已配 MCP 且地理编码命中） | 生成行程并落库 | 不变（MCP 命中即有效） |
| `去火星玩3天`（未配 MCP） | 生成模板行程并落库 | 返回引导文案，不落库 |

## 6. 验收清单

- [ ] `ToolCenter.validate_destination("")` / `"   "` 返回 `False`
- [ ] 未配置 MCP 时，北京 / 上海 / 杭州 / 成都 / 广州 / 深圳返回 `True`，其他城市返回 `False`
- [ ] 配置 MCP 且命中时，白名单外城市返回 `True`
- [ ] 配置 MCP 但未命中时，仍按白名单判断
- [ ] MCP 抛异常时，按白名单判断且不向外抛异常
- [ ] `generate_itinerary` 在生成前校验，无效时抛 `DestinationInvalidError`
- [ ] Scheduler 捕获后返回 `action="chat"`、`trip=None`，且 `memory.list_trips()` 不增加
- [ ] 错误码 `1003` 已登记在 `docs/schemas/api-response.md`
