"""跨模块共享的 Pydantic 模型层。

本层不属于 OODA 五模块，只做类型契约定义，不承载任何业务逻辑，
被 `api/` 与 `src/` 下各模块单向依赖。

收录标准：**凡被两个以上模块引用的模型都归属本层**，以保证同级模块之间
不出现横向 import。理由见 docs/sdd/models.md。
"""

from .api_response import ApiResponse
from .chat import ChatRequest
from .intent import Intent
from .itinerary import DailyPlan, Spot, TripPlan, WeatherInfo
from .tool_results import Attraction, CostResult, RouteResult
from .trip import DeleteTripResult, TripCard, TripRecord

__all__ = [
    "ApiResponse",
    "Attraction",
    "ChatRequest",
    "CostResult",
    "DailyPlan",
    "DeleteTripResult",
    "Intent",
    "RouteResult",
    "Spot",
    "TripCard",
    "TripPlan",
    "TripRecord",
    "WeatherInfo",
]
