"""行程的持久化记录与前端展示模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .itinerary import DailyPlan, TripPlan, WeatherInfo
from .tool_results import CostResult


class TripRecord(BaseModel):
    """一条已保存的行程记录，对应 SQLite `trips` 表的一行。

    由 MemoryStore 产出，见 docs/module-sdd/memory.md。
    """

    trip_id: str = Field(min_length=1)
    created_at: datetime
    destination: str = Field(min_length=1)
    days: int = Field(ge=1)
    budget: float | None = Field(default=None, ge=0)
    intent_raw: str = Field(description="用户原始输入，用于检索命中时展示")
    itinerary: TripPlan
    degraded: bool = False


class TripCard(BaseModel):
    """前端行程卡片结构（对应 FR-4）。

    由 Bridge.to_trip_card() 产出，是前端渲染的唯一契约 ——
    前端不直接消费 TripPlan。
    """

    trip_id: str = Field(
        default="",
        description="行程 ID，前端删除 / 修改的操作句柄（对应 AC-2）。保留默认空串"
        "是为容纳尚无 ID 的场景：Bridge.to_trip_card() 的入参是 TripPlan，而 trip_id"
        " 由 MemoryStore 落库时才生成，故由调用方在保存后回填（Scheduler 传 TripRecord"
        " 的 ID，RESTful 列表接口传 TripRecord 的 ID）。空串表示该卡片不可操作。",
    )
    destination: str = Field(min_length=1)
    days: int = Field(ge=1)
    total_cost: float = Field(ge=0, description="总预算（元）")
    degraded: bool = Field(default=False, description="为 true 时前端需展示降级提示")
    daily: list[DailyPlan] = Field(default_factory=list)
    weather_report: list[WeatherInfo] = Field(
        default_factory=list, description="多日天气报告（与出发日期对齐）"
    )
    cost_breakdown: CostResult | None = Field(
        default=None, description="花费分项（门票 / 交通 / 住宿 / 餐饮）"
    )
    source: str = Field(default="unknown", description="行程来源标识：llm / template / unknown")


class DeleteTripResult(BaseModel):
    """删除行程的显式结果，替代仅靠 code / msg 表达的隐式成功。"""

    trip_id: str = Field(min_length=1, description="被删除的行程 ID")
    deleted: bool = Field(default=True, description="是否确实删除成功")
