"""ToolCenter 单测。

对应 SDD docs/module-sdd/tools.md（含 §0 Mock 实现状态说明）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.exceptions import ToolCallError
from src.mcp_client import ENV_AMAP_MCP_KEY, ENV_AMAP_MCP_URL
from src.models import Attraction, CostResult, RouteResult, WeatherInfo
from src.tools import (
    DEFAULT_ATTRACTIONS,
    DEFAULT_COST_PER_DAY,
    DEFAULT_WEATHER_PATTERN,
    MOCK_CITY_WHITELIST,
    TOOL_DEFINITIONS,
    WEATHER_PATTERN,
    ToolCenter,
    _attractions,
    _build_mcp_client_from_env,
    _cost,
    _route,
    _weather,
)

BUILTIN_TOOLS = ["attractions", "cost", "route", "weather"]


@pytest.fixture
def center() -> ToolCenter:
    return ToolCenter()


@pytest.fixture(autouse=True)
def _without_mcp_env(monkeypatch) -> None:
    """默认清掉 MCP 环境变量，让 Mock 用例不受开发机配置干扰。

    `ToolCenter()` 会按 `AMAP_MCP_URL` 自动构建客户端；若本机恰好设了它，
    Mock 断言的用例会意外出网。需要走 MCP 的用例显式注入替身。
    """
    monkeypatch.delenv(ENV_AMAP_MCP_URL, raising=False)
    monkeypatch.delenv(ENV_AMAP_MCP_KEY, raising=False)


# ---------------------------------------------------------------- 注册表


def test_registers_four_builtin_tools(center: ToolCenter) -> None:
    assert sorted(center.list_tools()) == BUILTIN_TOOLS


def test_list_tools_returns_copy(center: ToolCenter) -> None:
    names = center.list_tools()
    names.append("伪造工具")
    assert "伪造工具" not in center.list_tools()


def test_register_duplicate_raises(center: ToolCenter) -> None:
    with pytest.raises(ToolCallError, match="已注册"):
        center.register("weather", _weather)


def test_tool_definitions_match_registry(center: ToolCenter) -> None:
    """Function Calling 的工具名必须与注册表严格一致。"""
    defined = {item["function"]["name"] for item in TOOL_DEFINITIONS}
    assert defined == set(center.list_tools())


def test_tool_definitions_use_openai_shape(center: ToolCenter) -> None:
    for item in TOOL_DEFINITIONS:
        assert item["type"] == "function"
        function = item["function"]
        assert function["name"] in center.list_tools()
        assert function["description"]
        assert function["parameters"]["type"] == "object"


async def test_register_then_call(center: ToolCenter) -> None:
    center.register("echo", _echo)
    assert await center.call("echo", value=7) == 7


async def test_register_async_tool_and_call(center: ToolCenter) -> None:
    """注册表同时支持同步与异步工具，调用方统一 await。"""
    center.register("echo_async", _echo_async)
    assert await center.call("echo_async", value=9) == 9


async def test_call_unknown_tool_raises(center: ToolCenter) -> None:
    with pytest.raises(ToolCallError, match="未注册的工具"):
        await center.call("不存在的工具")


async def test_call_signature_mismatch_keeps_cause(center: ToolCenter) -> None:
    with pytest.raises(ToolCallError, match="参数与签名不匹配") as excinfo:
        await center.call("weather", city="北京")  # 缺 days
    assert isinstance(excinfo.value.__cause__, TypeError)


async def test_call_wraps_tool_exception(center: ToolCenter) -> None:
    center.register("boom", _boom)

    with pytest.raises(ToolCallError, match="执行失败") as excinfo:
        await center.call("boom")

    assert isinstance(excinfo.value.__cause__, RuntimeError)


async def test_call_wraps_async_tool_exception(center: ToolCenter) -> None:
    center.register("boom_async", _boom_async)

    with pytest.raises(ToolCallError, match="执行失败") as excinfo:
        await center.call("boom_async")

    assert isinstance(excinfo.value.__cause__, RuntimeError)


async def test_call_preserves_agent_error(center: ToolCenter) -> None:
    center.register("bad", _raise_tool_call_error)

    with pytest.raises(ToolCallError, match="工具内部原始信息"):
        await center.call("bad")


def _echo(value: int) -> int:
    return value


async def _echo_async(value: int) -> int:
    return value


async def _boom_async() -> None:
    raise RuntimeError("异步底层炸了")


def _boom() -> None:
    raise RuntimeError("底层炸了")


def _raise_tool_call_error() -> None:
    raise ToolCallError("工具内部原始信息")


# ---------------------------------------------------------------- weather


async def test_weather_returns_pydantic_models() -> None:
    report = await _weather("北京", 3)
    assert len(report) == 3
    assert all(isinstance(item, WeatherInfo) for item in report)


async def test_weather_dates_start_today_and_are_consecutive() -> None:
    report = await _weather("上海", 4)
    today = date.today()
    assert [item.date for item in report] == [today + timedelta(days=i) for i in range(4)]


async def test_weather_is_stable_for_same_city_and_date() -> None:
    assert await _weather("杭州", 7) == await _weather("杭州", 7)


async def test_weather_rotates_through_declared_pattern() -> None:
    pattern = WEATHER_PATTERN["北京"]
    report = await _weather("北京", 7)
    start = report[0].date.toordinal() % len(pattern)
    expected = [pattern[(start + i) % len(pattern)] for i in range(7)]

    got = [(item.condition, item.temp_high, item.temp_low) for item in report]
    assert got == expected


def test_weather_pattern_matches_spec_for_beijing() -> None:
    assert WEATHER_PATTERN["北京"] == [
        ("晴", 28.0, 22.0),
        ("多云", 26.0, 21.0),
        ("阴", 25.0, 20.0),
        ("晴", 29.0, 23.0),
        ("多云", 27.0, 22.0),
        ("晴", 30.0, 24.0),
        ("阴", 26.0, 21.0),
    ]


async def test_weather_unknown_city_falls_back_to_default_pattern() -> None:
    report = await _weather("某不存在城市", 7)
    start = report[0].date.toordinal() % len(DEFAULT_WEATHER_PATTERN)
    expected = [DEFAULT_WEATHER_PATTERN[(start + i) % 7] for i in range(7)]

    assert [(item.condition, item.temp_high, item.temp_low) for item in report] == expected


@pytest.mark.parametrize("days", [0, -1, 31])
async def test_weather_rejects_out_of_range_days(days: int) -> None:
    with pytest.raises(ToolCallError, match="天数"):
        await _weather("北京", days)


# ---------------------------------------------------------------- MCP 优先 + Mock 降级


class _FakeMCPClient:
    """`AmapMCPClient` 的最小替身，用于验证 ToolCenter 的数据源切换。"""

    def __init__(
        self,
        *,
        weather: list[WeatherInfo] | None = None,
        attractions: list[Attraction] | None = None,
        route: RouteResult | None = None,
        city_exists: bool = True,
        fail: bool = False,
    ) -> None:
        self._weather = weather
        self._attractions = attractions
        self._route = route
        self._city_exists = city_exists
        self._fail = fail
        self.closed = 0

    async def weather(self, city: str, days: int) -> list[WeatherInfo]:
        if self._fail:
            raise RuntimeError("MCP 天气炸了")
        assert self._weather is not None
        return self._weather

    async def attractions(self, city: str, limit: int = 10) -> list[Attraction]:
        if self._fail:
            raise RuntimeError("MCP 景点炸了")
        assert self._attractions is not None
        return self._attractions

    async def route(self, city: str, spots: list[str]) -> RouteResult:
        if self._fail:
            raise RuntimeError("MCP 路线炸了")
        assert self._route is not None
        return self._route

    async def city_exists(self, city: str) -> bool:
        if self._fail:
            raise RuntimeError("MCP 校验炸了")
        return self._city_exists

    async def close(self) -> None:
        self.closed += 1


async def test_weather_tool_prefers_mcp_result() -> None:
    mcp_weather = [WeatherInfo(date=date(2026, 10, 3), condition="MCP晴")]
    center = ToolCenter(_FakeMCPClient(weather=mcp_weather))  # type: ignore[arg-type]

    assert await center.call("weather", city="北京", days=1) == mcp_weather


async def test_weather_tool_falls_back_to_mock_when_mcp_fails() -> None:
    center = ToolCenter(_FakeMCPClient(fail=True))  # type: ignore[arg-type]

    report = await center.call("weather", city="北京", days=2)

    assert [item.condition for item in report] == [
        WEATHER_PATTERN["北京"][(date.today() + timedelta(days=i)).toordinal() % 7][0]
        for i in range(2)
    ]


async def test_attractions_tool_prefers_mcp_result() -> None:
    mcp_items = [Attraction(name="MCP景点", city="北京", ticket_price=1.0)]
    center = ToolCenter(_FakeMCPClient(attractions=mcp_items))  # type: ignore[arg-type]

    assert await center.call("attractions", city="北京", limit=1) == mcp_items


async def test_attractions_tool_falls_back_to_mock_when_mcp_fails() -> None:
    center = ToolCenter(_FakeMCPClient(fail=True))  # type: ignore[arg-type]

    names = [item.name for item in await center.call("attractions", city="北京", limit=2)]

    assert names == ["故宫", "天安门"]


async def test_route_tool_prefers_mcp_result() -> None:
    mcp_route = RouteResult(
        city="北京", ordered_spots=["故宫", "天坛"], transport="驾车", total_distance_km=9.9
    )
    center = ToolCenter(_FakeMCPClient(route=mcp_route))  # type: ignore[arg-type]

    assert await center.call("route", city="北京", spots=["故宫", "天坛"]) == mcp_route


async def test_route_tool_falls_back_to_mock_when_mcp_fails() -> None:
    center = ToolCenter(_FakeMCPClient(fail=True))  # type: ignore[arg-type]

    result = await center.call("route", city="北京", spots=["故宫", "天坛"])

    assert result.total_distance_km == 1.5


async def test_validate_destination_rejects_blank() -> None:
    assert await ToolCenter().validate_destination("   ") is False


async def test_validate_destination_uses_whitelist_without_mcp() -> None:
    center = ToolCenter()

    assert await center.validate_destination("北京") is True
    assert await center.validate_destination("不存在的城市") is False
    assert "北京" in MOCK_CITY_WHITELIST


async def test_validate_destination_trusts_mcp_hit() -> None:
    center = ToolCenter(_FakeMCPClient(city_exists=True))  # type: ignore[arg-type]

    # MCP 命中时即使不在 Mock 白名单也视为有效
    assert await center.validate_destination("西安") is True


async def test_validate_destination_falls_back_to_whitelist_on_mcp_miss() -> None:
    center = ToolCenter(_FakeMCPClient(city_exists=False))  # type: ignore[arg-type]

    assert await center.validate_destination("北京") is True
    assert await center.validate_destination("西安") is False


async def test_validate_destination_falls_back_on_mcp_exception() -> None:
    center = ToolCenter(_FakeMCPClient(fail=True))  # type: ignore[arg-type]

    assert await center.validate_destination("上海") is True
    assert await center.validate_destination("西安") is False


async def test_close_is_idempotent_and_closes_mcp() -> None:
    mcp = _FakeMCPClient()
    center = ToolCenter(mcp)  # type: ignore[arg-type]

    await center.close()
    await center.close()

    assert mcp.closed == 2  # ToolCenter 不缓存关闭状态，交给客户端自身幂等


def test_build_mcp_client_from_env_returns_none_without_url() -> None:
    assert _build_mcp_client_from_env() is None


def test_build_mcp_client_from_env_builds_with_url(monkeypatch) -> None:
    monkeypatch.setenv(ENV_AMAP_MCP_URL, "https://mcp.amap.com/mcp")

    client = _build_mcp_client_from_env()

    assert client is not None


# ---------------------------------------------------------------- attractions


def test_attractions_preserve_heat_order() -> None:
    names = [item.name for item in _attractions("北京")]
    assert names == ["故宫", "天安门", "长城", "颐和园", "南锣鼓巷", "798艺术区", "天坛"]


def test_attractions_respect_limit() -> None:
    assert len(_attractions("上海", limit=2)) == 2


def test_attractions_unknown_city_uses_default_pool() -> None:
    names = [item.name for item in _attractions("某不存在城市")]
    assert names == DEFAULT_ATTRACTIONS


def test_attractions_returns_pydantic_models() -> None:
    items = _attractions("成都")
    assert all(isinstance(item, Attraction) for item in items)
    assert all(item.suggested_duration_min for item in items)


def test_attractions_rejects_non_positive_limit() -> None:
    with pytest.raises(ToolCallError, match="条数上限"):
        _attractions("北京", limit=0)


# ---------------------------------------------------------------- route


def test_route_preserves_input_order() -> None:
    result = _route("北京", ["天坛", "故宫", "长城"])
    assert isinstance(result, RouteResult)
    assert result.ordered_spots == ["天坛", "故宫", "长城"]


def test_route_travel_minutes_follow_hop_count() -> None:
    assert _route("北京", ["故宫", "天坛", "长城"]).total_travel_min == 60


def test_route_travel_distance_follows_transport_mode() -> None:
    # 2 个景点 → 步行（1 跳 × 1.5km）；3 个景点 → 地铁（2 跳 × 5km）
    assert _route("北京", ["故宫", "天坛"]).total_distance_km == 1.5
    assert _route("北京", ["故宫", "天坛", "长城"]).total_distance_km == 10.0
    assert _route("北京", ["故宫"]).total_distance_km == 0.0


@pytest.mark.parametrize(
    ("spots", "transport"),
    [(["故宫"], "步行"), (["故宫", "天坛"], "步行"), (["故宫", "天坛", "长城"], "地铁 + 步行")],
)
def test_route_recommends_transport(spots: list[str], transport: str) -> None:
    assert _route("北京", spots).transport == transport


def test_route_rejects_empty_spots() -> None:
    with pytest.raises(ToolCallError, match="景点列表为空"):
        _route("北京", [])


# ---------------------------------------------------------------- cost


def test_cost_defaults_to_medium_tier() -> None:
    result = _cost(days=2, spots=["故宫"])
    assert isinstance(result, CostResult)
    assert result.total == 2 * DEFAULT_COST_PER_DAY
    assert result.within_budget is None


def test_cost_picks_budget_tier_from_daily_budget() -> None:
    # 900 / 3 = 300 元/天 → 300 元档
    assert _cost(days=3, spots=[], budget=900).total == 900.0


def test_cost_picks_medium_tier_from_daily_budget() -> None:
    # 2400 / 2 = 1200 元/天 → 600 元档
    assert _cost(days=2, spots=[], budget=2400).total == 1200.0


def test_cost_picks_luxury_tier_from_daily_budget() -> None:
    # 2000 / 1 = 2000 元/天 → 1200 元档
    assert _cost(days=1, spots=[], budget=2000).total == 1200.0


def test_cost_within_budget_flag() -> None:
    # 预算 100 / 3 天 → 取最低的 300 元档 → 估算 900 元，超出预算
    assert _cost(days=3, spots=[], budget=100).within_budget is False
    assert _cost(days=1, spots=[], budget=500).within_budget is True


def test_cost_parts_sum_to_total() -> None:
    result = _cost(days=3, spots=[], budget=1800)
    assert (
        result.tickets_total
        + result.transport_total
        + result.lodging_total
        + result.food_total
        == result.total
    )


def test_cost_has_four_segments_with_expected_ratios() -> None:
    result = _cost(days=2, spots=[])

    assert result.total == 1200.0
    assert result.lodging_total == 540.0
    assert result.food_total == 300.0
    assert result.transport_total == 180.0
    assert result.tickets_total == 180.0


def test_cost_rejects_non_positive_days() -> None:
    with pytest.raises(ToolCallError, match="天数"):
        _cost(days=0, spots=[])
