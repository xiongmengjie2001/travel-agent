"""AmapMCPClient 单测。

对应 SDD docs/module-sdd/mcp.md：用 `httpx.MockTransport` 模拟高德 MCP
服务端，覆盖握手、会话头、JSON / SSE 两种响应、三个数据工具、城市校验与
关闭逻辑。**不发起任何真实网络请求**。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from src.exceptions import ToolCallError
from src.mcp_client import (
    MCP_CLIENT_NAME,
    MCP_PROTOCOL_VERSION,
    TOOL_DRIVING,
    TOOL_GEO,
    TOOL_TEXT_SEARCH,
    TOOL_WEATHER,
    AmapMCPClient,
    _derived_rating,
    _derived_ticket_price,
)

MCP_ENDPOINT = "https://mcp.test/mcp"
SESSION_ID = "session-abc"

Handler = Callable[[httpx.Request], httpx.Response]


def _rpc_payload(request: httpx.Request) -> dict[str, Any]:
    """读取请求体中的 JSON-RPC 消息。"""
    return json.loads(request.content.decode("utf-8"))


def _make_handler(
    tool_payloads: dict[str, Any],
    *,
    sse: bool = False,
    rpc_error: dict[str, Any] | None = None,
    is_error: bool = False,
) -> tuple[Handler, list[httpx.Request]]:
    """构造模拟高德 MCP 的处理器，并记录收到的全部请求。

    Args:
        tool_payloads: 工具名 → `content[].text` 的 JSON 载荷。
        sse: 为真时工具响应使用 `text/event-stream`。
        rpc_error: 非空时 `tools/call` 返回 JSON-RPC error。
        is_error: 为真时 `tools/call` 的 result 标记 `isError=true`。
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)

        body = _rpc_payload(request)
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "serverInfo": {"name": "amap-mock", "version": "1.0"},
                    },
                },
                headers={"Mcp-Session-Id": SESSION_ID},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)

        if method == "tools/call":
            if rpc_error is not None:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": body["id"], "error": rpc_error},
                )
            name = body["params"]["name"]
            result: dict[str, Any] = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(tool_payloads[name], ensure_ascii=False),
                    }
                ],
                "isError": is_error,
            }
            message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            if sse:
                return httpx.Response(
                    200,
                    content=f"event: message\ndata: {json.dumps(message)}\n\n",
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(
                200, json=message, headers={"content-type": "application/json"}
            )

        return httpx.Response(400, json={"error": f"未知方法 {method}"})

    return handler, requests


def _build_client(handler: Handler, *, api_key: str | None = None) -> AmapMCPClient:
    """用 MockTransport 构造客户端。"""
    return AmapMCPClient(
        MCP_ENDPOINT,
        api_key,
        transport=httpx.MockTransport(handler),
    )


def _calls(requests: list[httpx.Request]) -> list[dict[str, Any]]:
    """按 JSON-RPC 方法名筛选请求体。"""
    return [_rpc_payload(request) for request in requests]


# ---------------------------------------------------------------- 握手


async def test_handshake_sends_initialize_then_notification() -> None:
    handler, requests = _make_handler({TOOL_WEATHER: {"casts": []}})
    client = _build_client(handler)

    with pytest.raises(ToolCallError):
        await client.weather("北京", 1)

    methods = [body.get("method") for body in _calls(requests)]
    assert methods[:2] == ["initialize", "notifications/initialized"]

    initialize = _calls(requests)[0]
    assert initialize["params"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert initialize["params"]["clientInfo"]["name"] == MCP_CLIENT_NAME
    assert initialize["params"]["clientInfo"]["version"]

    await client.close()


async def test_handshake_happens_only_once() -> None:
    handler, requests = _make_handler(
        {
            TOOL_WEATHER: {
                "casts": [
                    {"date": "2026-10-25", "dayweather": "晴"},
                ]
            }
        }
    )
    client = _build_client(handler)

    await client.weather("北京", 1)
    await client.weather("北京", 1)

    methods = [body.get("method") for body in _calls(requests)]
    assert methods.count("initialize") == 1
    assert methods.count("notifications/initialized") == 1
    await client.close()


async def test_session_id_is_sent_on_subsequent_requests() -> None:
    handler, requests = _make_handler(
        {TOOL_WEATHER: {"casts": [{"date": "2026-10-25", "dayweather": "晴"}]}}
    )
    client = _build_client(handler)

    await client.weather("北京", 1)

    tool_call = next(
        request for request in requests if _rpc_payload(request).get("method") == "tools/call"
    )
    assert tool_call.headers.get("Mcp-Session-Id") == SESSION_ID
    await client.close()


async def test_api_key_is_appended_to_url() -> None:
    handler, requests = _make_handler({TOOL_GEO: {"geocodes": []}})
    client = _build_client(handler, api_key="secret-key")

    assert await client.city_exists("北京") is False

    assert requests[0].url.params["key"] == "secret-key"
    await client.close()


# ---------------------------------------------------------------- weather


async def test_weather_maps_casts_to_pydantic_models() -> None:
    handler, _ = _make_handler(
        {
            TOOL_WEATHER: {
                "casts": [
                    {
                        "date": "2026-10-25",
                        "dayweather": "晴",
                        "daytemp": "26",
                        "nighttemp": "15",
                    },
                    {
                        "date": "2026/10/26",
                        "dayweather": "多云",
                        "daytemp": 24,
                        "nighttemp": 14,
                    },
                ]
            }
        }
    )
    client = _build_client(handler)

    report = await client.weather("北京", 2)

    assert [item.date for item in report] == [date(2026, 10, 25), date(2026, 10, 26)]
    assert [item.condition for item in report] == ["晴", "多云"]
    assert report[0].temp_high == 26.0
    assert report[0].temp_low == 15.0
    await client.close()


async def test_weather_supports_forecasts_shell() -> None:
    handler, _ = _make_handler(
        {
            TOOL_WEATHER: {
                "forecasts": [
                    {
                        "city": "北京",
                        "casts": [{"date": "2026-10-25", "dayweather": "阴"}],
                    }
                ]
            }
        }
    )
    client = _build_client(handler)

    report = await client.weather("北京", 1)

    assert report[0].condition == "阴"
    await client.close()


async def test_weather_supports_sse_response() -> None:
    handler, _ = _make_handler(
        {TOOL_WEATHER: {"casts": [{"date": "2026-10-25", "dayweather": "晴"}]}},
        sse=True,
    )
    client = _build_client(handler)

    report = await client.weather("北京", 1)

    assert report[0].date == date(2026, 10, 25)
    await client.close()


async def test_weather_rejects_insufficient_days() -> None:
    handler, _ = _make_handler(
        {TOOL_WEATHER: {"casts": [{"date": "2026-10-25", "dayweather": "晴"}]}}
    )
    client = _build_client(handler)

    with pytest.raises(ToolCallError, match="不足"):
        await client.weather("北京", 2)
    await client.close()


@pytest.mark.parametrize("days", [0, 31])
async def test_weather_rejects_out_of_range_days_without_network(days: int) -> None:
    handler, requests = _make_handler({})

    with pytest.raises(ToolCallError, match="天数"):
        await _build_client(handler).weather("北京", days)

    assert requests == []


# ---------------------------------------------------------------- attractions


async def test_attractions_maps_poi_fields() -> None:
    handler, _ = _make_handler(
        {
            TOOL_TEXT_SEARCH: {
                "pois": [
                    {
                        "name": "故宫",
                        "cityname": "北京",
                        "biz_ext": {"cost": "60", "rating": "4.8", "duration": "180"},
                    },
                    {"name": "天坛"},
                ]
            }
        }
    )
    client = _build_client(handler)

    items = await client.attractions("北京", limit=2)

    assert [item.name for item in items] == ["故宫", "天坛"]
    assert items[0].ticket_price == 60.0
    assert items[0].rating == 4.8
    assert items[0].suggested_duration_min == 180
    # 缺失字段按名称确定性派生，且与独立计算的派生值一致
    assert items[1].ticket_price == _derived_ticket_price("天坛")
    assert items[1].rating == _derived_rating("天坛")
    assert items[1].suggested_duration_min == 120
    await client.close()


async def test_attractions_respects_limit_and_search_arguments() -> None:
    handler, requests = _make_handler(
        {TOOL_TEXT_SEARCH: {"pois": [{"name": f"景点{i}"} for i in range(5)]}}
    )
    client = _build_client(handler)

    items = await client.attractions("上海", limit=2)

    assert len(items) == 2
    tool_call = next(
        body
        for body in _calls(requests)
        if body.get("method") == "tools/call"
    )
    assert tool_call["params"] == {
        "name": TOOL_TEXT_SEARCH,
        "arguments": {"keywords": "景点", "city": "上海", "citylimit": True},
    }
    await client.close()


async def test_attractions_empty_result_raises() -> None:
    handler, _ = _make_handler({TOOL_TEXT_SEARCH: {"pois": []}})
    client = _build_client(handler)

    with pytest.raises(ToolCallError, match="为空"):
        await client.attractions("北京")
    await client.close()


async def test_attractions_rejects_non_positive_limit_without_network() -> None:
    handler, requests = _make_handler({})

    with pytest.raises(ToolCallError, match="条数上限"):
        await _build_client(handler).attractions("北京", limit=0)

    assert requests == []


# ---------------------------------------------------------------- route


async def test_route_geocodes_spots_and_sums_segments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _rpc_payload(request)
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                headers={"Mcp-Session-Id": SESSION_ID},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)

        arguments = body["params"]["arguments"]
        name = body["params"]["name"]
        if name == TOOL_GEO:
            location = "116.39,39.90" if arguments["address"] == "故宫" else "116.40,39.88"
            payload: Any = {"geocodes": [{"location": location}]}
        else:
            assert name == TOOL_DRIVING
            payload = {"route": {"paths": [{"distance": 5000, "duration": 600}]}}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
            },
        )

    client = _build_client(handler, api_key="k")

    result = await client.route("北京", ["故宫", "天坛"])

    assert result.city == "北京"
    assert result.ordered_spots == ["故宫", "天坛"]
    assert result.transport == "驾车"
    assert result.total_distance_km == 5.0
    assert result.total_travel_min == 10
    await client.close()


async def test_route_unlocatable_spot_raises() -> None:
    handler, _ = _make_handler({TOOL_GEO: {"geocodes": []}})
    client = _build_client(handler)

    with pytest.raises(ToolCallError, match="无法定位"):
        await client.route("北京", ["虚构景点"])
    await client.close()


async def test_route_rejects_empty_spots_without_network() -> None:
    handler, requests = _make_handler({})

    with pytest.raises(ToolCallError, match="景点列表为空"):
        await _build_client(handler).route("北京", [])

    assert requests == []


# ---------------------------------------------------------------- city_exists


async def test_city_exists_true_when_geocode_hit() -> None:
    handler, _ = _make_handler(
        {TOOL_GEO: {"geocodes": [{"location": "116.39,39.90"}]}}
    )
    client = _build_client(handler)

    assert await client.city_exists("北京") is True
    await client.close()


async def test_city_exists_false_when_geocode_miss() -> None:
    handler, _ = _make_handler({TOOL_GEO: {"geocodes": []}})
    client = _build_client(handler)

    assert await client.city_exists("不存在的城市") is False
    await client.close()


async def test_city_exists_false_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连不上", request=request)

    client = _build_client(handler)

    assert await client.city_exists("北京") is False
    await client.close()


async def test_city_exists_false_for_blank_without_network() -> None:
    handler, requests = _make_handler({})

    assert await _build_client(handler).city_exists("   ") is False
    assert requests == []


# ---------------------------------------------------------------- 错误与关闭


async def test_rpc_error_is_wrapped() -> None:
    handler, _ = _make_handler(
        {},
        rpc_error={"code": -32601, "message": "method not found"},
    )
    client = _build_client(handler)

    with pytest.raises(ToolCallError, match=r"\[-32601\] method not found"):
        await client.weather("北京", 1)
    await client.close()


async def test_tool_is_error_flag_raises() -> None:
    handler, _ = _make_handler(
        {TOOL_WEATHER: {"casts": []}},
        is_error=True,
    )
    client = _build_client(handler)

    with pytest.raises(ToolCallError, match="返回错误"):
        await client.weather("北京", 1)
    await client.close()


async def test_http_status_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _build_client(handler)

    with pytest.raises(ToolCallError, match="HTTP 500"):
        await client.weather("北京", 1)
    await client.close()


async def test_close_ends_session_and_is_idempotent() -> None:
    handler, requests = _make_handler(
        {TOOL_WEATHER: {"casts": [{"date": "2026-10-25", "dayweather": "晴"}]}}
    )
    client = _build_client(handler)
    await client.weather("北京", 1)

    await client.close()
    delete_requests = [request for request in requests if request.method == "DELETE"]
    assert len(delete_requests) == 1
    assert delete_requests[0].headers.get("Mcp-Session-Id") == SESSION_ID

    await client.close()
    assert len([request for request in requests if request.method == "DELETE"]) == 1
    assert client._client is None
    assert client._session_id is None


async def test_close_without_connection_is_noop() -> None:
    handler, requests = _make_handler({})

    await _build_client(handler).close()

    assert requests == []
