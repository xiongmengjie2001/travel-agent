"""API 统一响应包裹结构。

对应 docs/schemas/api-response.md 与 PRD AC-3。
被 `api/` 层与 Bridge 共同引用，故归属共享模型层。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """所有 API 端点的统一返回结构。

    `code == 0` 表示成功；失败时 `data` 必须为 `None`。
    降级路径（DeepSeek 不可用但模板可用）仍返回 `code == 0`，
    通过 `data.degraded == True` 区分（对应 AC-5）。
    """

    code: int = 0
    msg: str = "ok"
    data: T | None = None

    @classmethod
    def ok(cls, data: T, msg: str = "ok") -> "ApiResponse[T]":
        return cls(code=0, msg=msg, data=data)

    @classmethod
    def fail(cls, code: int, msg: str) -> "ApiResponse[None]":
        return cls(code=code, msg=msg, data=None)
