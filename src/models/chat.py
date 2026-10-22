"""对话请求模型。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_LEN = 1000


class ChatRequest(BaseModel):
    """前端对话请求体（对应 FR-1 / FR-4）。

    由 Bridge.parse_chat_request() 产出。
    """

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)
    session_id: str | None = Field(
        default=None, description="连续对话的会话标识（对应 FR-4 的追问与修改）"
    )
    start_date: date | None = Field(
        default=None,
        description="出发日期（ISO 8601，如 2026-10-25）；未提供时由意图解析或默认明天决定",
    )

    @field_validator("message")
    @classmethod
    def _strip_and_reject_blank(cls, v: str) -> str:
        """去除首尾空白并拒绝纯空白输入（对应 NFR-3 的输入防护）。"""
        stripped = v.strip()
        if not stripped:
            raise ValueError("message 不能为空白")
        return stripped
