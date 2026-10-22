"""业务异常体系。

`src/` 下所有模块**只抛本文件中的异常子类**，由 `api/` 层统一捕获并转换为
`ApiResponse`。禁止在 `src/` 内抛出 `HTTPException`（CLAUDE.md 编码规范）。

定义与错误码表的对应关系见 docs/schemas/api-response.md。
"""

from __future__ import annotations

__all__ = [
    "AgentError",
    "DestinationInvalidError",
    "IntentUnrecognizedError",
    "InvalidRequestError",
    "LLMUnavailableError",
    "MemoryStoreError",
    "ToolCallError",
    "TripNotFoundError",
]


class AgentError(Exception):
    """所有业务异常的基类，携带对外错误码与 HTTP 状态码。

    子类通过覆盖 `code` / `http_status` 类属性声明自己的对外语义，
    不要覆盖 `__init__`。
    """

    code: int = 5000
    http_status: int = 500

    def __init__(self, msg: str) -> None:
        super().__init__(msg)
        self.msg = msg


class InvalidRequestError(AgentError):
    """请求参数校验失败（含 Pydantic ValidationError 的转换结果）。"""

    code: int = 1001
    http_status: int = 400


class IntentUnrecognizedError(AgentError):
    """无法从用户输入中识别出有效意图或目的地。"""

    code: int = 1002
    http_status: int = 200


class DestinationInvalidError(AgentError):
    """目的地不存在或无法通过校验。

    这是用户输入侧的预期业务分支：Scheduler 会把它转换为一般对话引导文案，
    不生成、不落库行程；直接调用 `ReasoningEngine.generate_itinerary()` 的
    调用方则会收到本异常。
    """

    code: int = 1003
    http_status: int = 200


class LLMUnavailableError(AgentError):
    """LLM 调用失败，且降级方案也不可用。

    注意：DeepSeek 不可用但模板方案可用时**不抛本异常** —— 那属于预期内的
    降级路径，返回 code=0 且 data.degraded=True（对应 AC-5）。
    """

    code: int = 2001
    http_status: int = 200


class ToolCallError(AgentError):
    """工具或 MCP 服务调用失败。"""

    code: int = 2002
    http_status: int = 200


class TripNotFoundError(AgentError):
    """指定 trip_id 的行程不存在。"""

    code: int = 3001
    http_status: int = 200


class MemoryStoreError(AgentError):
    """记忆库读写失败（磁盘、写锁超时、序列化等）。"""

    code: int = 3002
    http_status: int = 200
