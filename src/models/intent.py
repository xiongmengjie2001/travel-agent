"""用户意图解析结果。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Intent(BaseModel):
    """`ReasoningEngine.parse_intent()` 的产出，Scheduler 据此决定本轮 Action。

    字段为 `None` 表示**未能从输入中解析出该项**，而不是「取默认值」——
    调用方必须区分这两种情况（例如 `days is None` 时不应擅自按 3 天生成）。
    """

    raw_text: str = Field(min_length=1, description="用户原始输入")
    destination: str | None = Field(default=None, description="目的地，未识别时为 None")
    days: int | None = Field(default=None, ge=1, le=30)
    start_date: date | None = Field(
        default=None, description="出发日期；解析后恒有值（默认明天），None 仅表示未赋值"
    )
    budget: float | None = Field(default=None, ge=0, description="预算（元）")
    preferences: list[str] = Field(default_factory=list, description="偏好标签，如 亲子 / 美食")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="解析置信度")
