"""行程领域模型：行程本体及其组成结构。

被 ReasoningEngine（产出）、MemoryStore（持久化）、Bridge（转换）共同引用。
"""

from __future__ import annotations

from datetime import date as Date

from pydantic import BaseModel, Field, model_validator

from .tool_results import CostResult


class WeatherInfo(BaseModel):
    """单日天气，数据来自 MCP 天气工具（对应 FR-3）。"""

    date: Date
    condition: str = Field(description="天气状况，如 晴 / 多云 / 小雨")
    temp_high: float | None = Field(default=None, description="最高气温（℃）")
    temp_low: float | None = Field(default=None, description="最低气温（℃）")


class Spot(BaseModel):
    """单个景点。"""

    name: str = Field(min_length=1)
    duration_min: int = Field(default=120, ge=0, description="建议游玩时长（分钟）")
    ticket_price: float = Field(default=0.0, ge=0, description="门票价格（元）")


class DailyPlan(BaseModel):
    """单日行程安排。

    `spots` 的列表顺序即当日的游览路线顺序（对应 AC-1 的「路线顺序」），
    调用方不得重排。
    """

    day_index: int = Field(ge=1, description="第几天，从 1 开始")
    date: Date | None = Field(default=None, description="该日日期，未指定时为空")
    spots: list[Spot] = Field(default_factory=list)
    estimated_cost: float = Field(default=0.0, ge=0, description="当日预估花费（元）")
    weather: WeatherInfo | None = Field(default=None)


class TripPlan(BaseModel):
    """完整行程方案。ReasoningEngine 的产出，MemoryStore 的持久化对象。"""

    destination: str = Field(min_length=1)
    days: int = Field(ge=1, le=30)
    budget: float | None = Field(default=None, ge=0, description="用户预算（元），未提供时为空")
    total_cost: float = Field(default=0.0, ge=0, description="预估总花费（元）")
    daily: list[DailyPlan] = Field(default_factory=list)
    degraded: bool = Field(default=False, description="是否来自降级模板方案（对应 AC-5）")
    weather_report: list[WeatherInfo] = Field(
        default_factory=list, description="多日天气报告（与出发日期对齐）"
    )
    cost_breakdown: CostResult | None = Field(
        default=None, description="花费分项（门票 / 交通 / 住宿 / 餐饮）"
    )
    source: str = Field(
        default="unknown", description="行程来源标识：llm / template / unknown"
    )

    @model_validator(mode="after")
    def _daily_must_not_exceed_days(self) -> TripPlan:
        """`daily` 不得多于 `days`。

        只约束**上界**，不要求相等：`daily == []` 是合法输入（调用方可能只关心
        行程抬头，测试夹具也这么用）。多于 `days` 则直接违背 `days` 的语义
        ——前端按 `days` 渲染天数，多出来的日程无处安放。

        加这道校验是因为生产方（`ReasoningEngine._parse_llm_plan`）此前用
        `model_copy` 回填 `days`，而 `model_copy` 默认不触发校验，导致
        「days=5、daily 只有 3 天」的行程能一路入库。
        """
        if len(self.daily) > self.days:
            raise ValueError(
                f"daily 有 {len(self.daily)} 天，超过 days={self.days}"
            )
        return self
