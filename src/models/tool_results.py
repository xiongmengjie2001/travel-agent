"""工具调用的返回模型。

由 ToolCenter 的内置工具产出，被 ReasoningEngine 消费。

字段规格见 docs/module-sdd/tools.md §0「工具返回模型的字段规格」。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Attraction(BaseModel):
    """景点条目，由 `attractions` 工具产出。"""

    name: str = Field(min_length=1)
    city: str | None = Field(default=None, description="所属城市")
    ticket_price: float | None = Field(default=None, ge=0, description="门票价格（元）")
    rating: float | None = Field(
        default=None, ge=0, le=5, description="评分（0–5）；数据源缺失时由名称派生"
    )
    suggested_duration_min: int | None = Field(
        default=None, ge=0, description="建议游玩时长（分钟）"
    )


class RouteResult(BaseModel):
    """游览顺序与交通方式，由 `route` 工具产出。"""

    city: str = Field(min_length=1)
    ordered_spots: list[str] = Field(
        default_factory=list, description="按游览顺序排列的景点名，消费方不得重排"
    )
    transport: str | None = Field(default=None, description="推荐的交通方式")
    total_travel_min: int | None = Field(default=None, ge=0, description="在途总时长（分钟）")
    total_distance_km: float | None = Field(
        default=None, ge=0, description="景点之间总行驶距离（公里）"
    )


class CostResult(BaseModel):
    """花费估算，由 `cost` 工具产出。

    v3.0 起升级为四段分项：门票 / 市内交通 / 住宿 / 餐饮，四项之和恒等于
    `total`（门票取余额，吸收其余三项的舍入误差）。
    """

    days: int = Field(ge=1)
    tickets_total: float = Field(default=0.0, ge=0, description="门票合计（元）")
    transport_total: float = Field(default=0.0, ge=0, description="市内交通合计（元）")
    lodging_total: float = Field(default=0.0, ge=0, description="住宿合计（元）")
    food_total: float = Field(default=0.0, ge=0, description="餐饮合计（元）")
    total: float = Field(default=0.0, ge=0, description="总计（元）")
    within_budget: bool | None = Field(
        default=None, description="是否在用户预算内；未提供预算时为 None"
    )
