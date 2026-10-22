# Schema：ApiResponse

> 对应 PRD [AC-3](../PRD.md)、系统设计 [第 5 节](../system-design.md)
> 版本：v1.0
> 日期：2026-10-03

## 1. 契约

所有 API 端点返回统一包裹结构，业务数据一律放在 `data` 内。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `code` | `int` | 必填，默认 `0` | `0` 表示成功，非 `0` 见错误码表 |
| `msg` | `str` | 必填，默认 `"ok"` | 成功时为 `"ok"`，失败时为人类可读描述 |
| `data` | `T \| null` | 泛型，失败时必须为 `null` | 业务载荷 |

## 2. Pydantic v2 定义

```python
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应包裹结构。"""

    code: int = 0
    msg: str = "ok"
    data: T | None = None

    @classmethod
    def ok(cls, data: T, msg: str = "ok") -> "ApiResponse[T]":
        return cls(code=0, msg=msg, data=data)

    @classmethod
    def fail(cls, code: int, msg: str) -> "ApiResponse[None]":
        return cls(code=code, msg=msg, data=None)
```

## 3. 错误码表

| code | 含义 | 抛出异常 | HTTP 状态 |
| --- | --- | --- | --- |
| `0` | 成功 | — | 200 |
| `1001` | 请求参数校验失败 | `InvalidRequestError` | 400 |
| `1002` | 用户意图无法识别 | `IntentUnrecognizedError` | 200 |
| `1003` | 目的地无效 | `DestinationInvalidError` | 200 |
| `2001` | LLM 调用失败且降级不可用 | `LLMUnavailableError` | 200 |
| `2002` | 工具 / MCP 调用失败 | `ToolCallError` | 200 |
| `3001` | 行程不存在 | `TripNotFoundError` | 200 |
| `3002` | 记忆库读写失败 | `MemoryStoreError` | 200 |
| `5000` | 未预期的内部错误 | 兜底捕获 | 500 |

**降级不占用错误码**：DeepSeek 不可用但模板方案可用时，返回 `code = 0`，仅通过 `data.degraded = true` 区分（PRD AC-5）。`2001` 仅用于「LLM 失败 **且** 降级路径也失败」的兜底情况。

## 4. 异常体系

对应 CLAUDE.md「自定义异常类，统一在 api 层捕获」：

```python
class AgentError(Exception):
    """所有业务异常的基类，携带对外错误码。"""

    code: int = 5000
    http_status: int = 500

    def __init__(self, msg: str) -> None:
        super().__init__(msg)
        self.msg = msg


class InvalidRequestError(AgentError):
    code, http_status = 1001, 400


class IntentUnrecognizedError(AgentError):
    code, http_status = 1002, 200


class DestinationInvalidError(AgentError):
    code, http_status = 1003, 200


class LLMUnavailableError(AgentError):
    code, http_status = 2001, 200


class ToolCallError(AgentError):
    code, http_status = 2002, 200


class TripNotFoundError(AgentError):
    code, http_status = 3001, 200


class MemoryStoreError(AgentError):
    code, http_status = 3002, 200
```

`src/` 内各模块只抛 `AgentError` 子类，**不得**抛出 `HTTPException`；由 `api/` 层的统一 handler 转换：

```python
@app.exception_handler(AgentError)
async def handle_agent_error(request: Request, exc: AgentError) -> JSONResponse:
    body = ApiResponse.fail(code=exc.code, msg=exc.msg)
    return JSONResponse(status_code=exc.http_status, content=body.model_dump())
```

## 5. HTTP 状态码策略

业务异常一律在响应体内用 `code` 表达，HTTP 状态码按上表 —— 除 `1001`（参数校验，400）和兜底 `5000`（500）外，其余业务错误均返回 **HTTP 200**。

理由：前端只需统一读取 `code` 判定业务结果，无需同时处理 HTTP 层与业务层两套判定逻辑。反向代理与日志也不会把预期的业务分支误记为服务故障。

## 6. 示例

成功：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "trip_id": "tr_20261003_8f3a",
    "destination": "北京",
    "days": 3,
    "total_cost": 2860,
    "degraded": false,
    "daily": []
  }
}
```

降级成功（仍为 `code: 0`）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": { "trip_id": "tr_20261003_9c1d", "degraded": true, "daily": [] }
}
```

失败：

```json
{ "code": 1002, "msg": "无法从输入中识别目的地，请补充后重试", "data": null }
```

## 7. 待确认

- 当前错误码按 `1xxx` 请求 / `2xxx` 依赖 / `3xxx` 数据分层分配，是否有既定的编码规范需对齐。
- 是否需要 `trace_id` 字段用于链路追踪；若需要，应加在顶层（与 `code` / `msg` 平级），而非 `data` 内。
