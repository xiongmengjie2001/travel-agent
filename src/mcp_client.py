"""高德地图 MCP 客户端。

封装 **JSON-RPC 2.0 over Streamable HTTP** 的 MCP 握手与工具调用：

1. 懒加载 `httpx.AsyncClient`，首次调用时才建立连接；
2. `initialize` → 记录 `Mcp-Session-Id` → `notifications/initialized`；
3. 以 `tools/call` 调用高德 MCP 工具，解析 `content[].text` 中的 JSON
   （同时兼容 `application/json` 与 `text/event-stream` 响应）；
4. `close()` 结束 MCP 会话并释放连接，可重复调用。

对外只暴露四个数据方法（weather / attractions / route / city_exists），
返回值一律为 Pydantic 模型；协议层异常统一收敛为 `ToolCallError`，由
`ToolCenter` 决定是否降级 Mock。

规格见 docs/module-sdd/mcp.md。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, timedelta
from typing import Any, Iterator

import httpx

from src.exceptions import ToolCallError
from src.models import Attraction, RouteResult, WeatherInfo

__all__ = [
    "ENV_AMAP_MCP_KEY",
    "ENV_AMAP_MCP_URL",
    "MCP_CLIENT_NAME",
    "MCP_CLIENT_VERSION",
    "MCP_PROTOCOL_VERSION",
    "MCP_TIMEOUT",
    "AmapMCPClient",
]

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------- 配置常量

ENV_AMAP_MCP_URL = "AMAP_MCP_URL"
ENV_AMAP_MCP_KEY = "AMAP_MCP_KEY"

MCP_TIMEOUT = 15.0
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "travel-agent2"
MCP_CLIENT_VERSION = "0.1.0"

# 高德 MCP 工具名（与高德服务端保持一致）。
TOOL_WEATHER = "maps_weather"
TOOL_TEXT_SEARCH = "maps_text_search"
TOOL_GEO = "maps_geo"
TOOL_DRIVING = "maps_direction_driving"

# 确定性派生值的参数（见 mcp.md §2.3）。
DERIVED_TICKET_BASE = 20.0
DERIVED_TICKET_STEP = 10.0
DERIVED_TICKET_RANGE = 15
DERIVED_RATING_BASE = 3.5
DERIVED_RATING_RANGE = 16
DERIVED_RATING_STEP = 0.1

DEFAULT_SPOT_DURATION_MIN = 120
MAX_FORECAST_DAYS = 30

# 天气响应里各字段的候选键：高德不同接口 / 版本命名不一致，按顺序取第一个。
_CONDITION_KEYS = ("dayweather", "day_weather", "dayWeather", "condition", "weather")
_HIGH_KEYS = ("daytemp", "day_temp", "dayTemp", "temp_high", "high")
_LOW_KEYS = ("nighttemp", "night_temp", "nightTemp", "temp_low", "low")
_DATE_KEYS = ("date", "day", "forecast_date")


class AmapMCPClient:
    """高德地图 MCP 客户端（懒加载、可关闭）。"""

    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        *,
        timeout: float = MCP_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """构造客户端，但**不建立连接**。

        Args:
            url: MCP 端点，如 `https://mcp.amap.com/mcp`。
            api_key: 高德 API Key；URL 已含 `key` 参数时可留空。
            timeout: 单次 HTTP 请求超时秒数。
            transport: 仅供测试注入 `httpx.MockTransport`，生产代码不传。
        """
        self._url = _with_api_key(url, api_key)
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._initialized = False
        self._request_id = 0
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------ 对外数据方法

    async def weather(self, city: str, days: int) -> list[WeatherInfo]:
        """查询逐日天气并映射为 `WeatherInfo`。

        Args:
            city: 城市名，如 `北京`。
            days: 需要的天数，调用方保证 1–30。

        Returns:
            长度为 `days` 的逐日天气。

        Raises:
            ToolCallError: 网络错误 / 超时 / JSON-RPC error / 响应不符契约。
        """
        if days < 1 or days > MAX_FORECAST_DAYS:
            raise ToolCallError(f"天气查询天数必须在 1–{MAX_FORECAST_DAYS} 之间，收到 {days}")
        payload = await self._call_tool(TOOL_WEATHER, {"city": city})
        return _parse_weather(payload, days)

    async def attractions(self, city: str, limit: int = 10) -> list[Attraction]:
        """关键词搜索城市景点，映射为 `Attraction`。

        POI 缺门票 / 评分时按名称确定性派生（见 mcp.md §2.3）。

        Args:
            city: 城市名。
            limit: 返回条数上限，调用方保证 ≥ 1。

        Returns:
            按高德返回顺序排列的景点列表。

        Raises:
            ToolCallError: 网络错误 / 超时 / JSON-RPC error / 响应不符契约。
        """
        if limit < 1:
            raise ToolCallError(f"景点条数上限必须 ≥ 1，收到 {limit}")
        payload = await self._call_tool(
            TOOL_TEXT_SEARCH,
            {"keywords": "景点", "city": city, "citylimit": True},
        )
        return _parse_attractions(payload, city, limit)

    async def route(self, city: str, spots: list[str]) -> RouteResult:
        """逐点地理编码后逐段规划驾车路线，汇总距离与时长。

        Args:
            city: 城市名。
            spots: 景点名列表，非空，顺序即游览顺序。

        Returns:
            `RouteResult`；`ordered_spots` 与输入等长同序，`transport="驾车"`。

        Raises:
            ToolCallError: 景点无法定位 / 路线调用失败 / 响应不符契约。
        """
        if not spots:
            raise ToolCallError("景点列表为空，无法规划路线")

        coordinates: list[str] = []
        for spot in spots:
            payload = await self._call_tool(TOOL_GEO, {"address": spot, "city": city})
            geocodes = _parse_geocodes(payload)
            if not geocodes:
                raise ToolCallError(f"景点无法定位: {spot}")
            coordinates.append(geocodes[0])

        distance_m = 0.0
        duration_s = 0.0
        for origin, destination in zip(coordinates, coordinates[1:]):
            payload = await self._call_tool(
                TOOL_DRIVING,
                {"origin": origin, "destination": destination},
            )
            segment_distance, segment_duration = _parse_driving_totals(payload)
            distance_m += segment_distance
            duration_s += segment_duration

        return RouteResult(
            city=city,
            ordered_spots=list(spots),
            transport="驾车",
            total_travel_min=int(round(duration_s / 60.0)),
            total_distance_km=round(distance_m / 1000.0, 2),
        )

    async def city_exists(self, city: str) -> bool:
        """用地理编码判断城市是否真实存在。

        任何异常（网络 / 超时 / 无结果 / 响应异常）都返回 `False`，**不抛出**。

        Args:
            city: 城市名。

        Returns:
            地理编码命中返回 `True`，否则 `False`。
        """
        cleaned = (city or "").strip()
        if not cleaned:
            return False
        try:
            payload = await self._call_tool(TOOL_GEO, {"address": cleaned, "city": cleaned})
            return bool(_parse_geocodes(payload))
        except Exception as exc:  # noqa: BLE001 —— 契约要求此方法绝不抛出
            LOGGER.warning("MCP 城市校验失败，按不存在处理: %s", exc)
            return False

    async def close(self) -> None:
        """结束 MCP 会话并关闭 HTTP 连接；可重复调用。"""
        client = self._client
        if client is None:
            return
        # 结束会话是 best-effort：服务端不支持 DELETE 或网络失败都不影响关闭。
        if self._session_id:
            try:
                await client.delete(
                    self._url,
                    headers={"Mcp-Session-Id": self._session_id},
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("结束 MCP 会话失败（忽略）: %s", exc)
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("关闭 MCP 连接失败（忽略）: %s", exc)
        finally:
            self._client = None
            self._session_id = None
            self._initialized = False

    # ------------------------------------------------------------ 内部协议

    async def _ensure_initialized(self) -> None:
        """确保 MCP 握手完成；并发首调由锁串行化。"""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            response = await self._post_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": MCP_CLIENT_NAME,
                            "version": MCP_CLIENT_VERSION,
                        },
                    },
                }
            )
            if "error" in response:
                raise ToolCallError(f"MCP 初始化失败: {_rpc_error_message(response)}")
            await self._post_rpc(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
            self._initialized = True
            LOGGER.info("MCP 握手完成（session=%s）", self._session_id or "无")

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用一个 MCP 工具并返回解析后的 JSON 载荷。

        Args:
            name: 高德 MCP 工具名。
            arguments: 工具参数（MCP 协议规定的对象）。

        Returns:
            工具 `content[].text` 解析出的 JSON（或 `structuredContent`）。

        Raises:
            ToolCallError: JSON-RPC error / `isError=true` / 载荷无法解析。
        """
        await self._ensure_initialized()
        response = await self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if "error" in response:
            raise ToolCallError(f"MCP 工具 {name} 调用失败: {_rpc_error_message(response)}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ToolCallError(f"MCP 工具 {name} 响应缺少 result 对象")
        return _extract_tool_payload(result)

    async def _post_rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送一条 JSON-RPC 消息并返回解析后的响应。

        握手后的通知（`notifications/initialized`）无响应体，调用方单独处理。

        Raises:
            ToolCallError: 网络错误 / 非 2xx / SSE 与 JSON 均无法解析。
        """
        client = self._get_client()
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else None
        try:
            response = await client.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ToolCallError(f"MCP 请求失败（HTTP {exc.response.status_code}）") from exc
        except Exception as exc:  # httpx 网络异常族 + 超时
            raise ToolCallError(f"MCP 请求失败: {exc}") from exc

        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        # 通知没有 id，服务端通常回 202/204 空体，无需解析。
        if "id" not in payload:
            return {}
        return self._decode_rpc(response, payload["id"])

    def _decode_rpc(self, response: httpx.Response, request_id: int) -> dict[str, Any]:
        """从 JSON 或 SSE 响应中取出与 `request_id` 匹配的 JSON-RPC 消息。

        Raises:
            ToolCallError: 响应体为空 / 无匹配消息 / JSON 无法解析。
        """
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            messages = list(_iter_sse_messages(response.text))
        else:
            text = (response.text or "").strip()
            if not text:
                raise ToolCallError("MCP 响应体为空")
            try:
                body = response.json()
            except ValueError as exc:
                raise ToolCallError(f"MCP 响应不是合法 JSON: {exc}") from exc
            messages = body if isinstance(body, list) else [body]

        for message in messages:
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise ToolCallError(f"MCP 响应中没有 id={request_id} 的消息")

    def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端（首次调用时创建）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        return self._client

    def _next_id(self) -> int:
        """生成严格递增的 JSON-RPC 请求 id。"""
        self._request_id += 1
        return self._request_id


# ---------------------------------------------------------------- 响应映射


def _extract_tool_payload(result: dict[str, Any]) -> Any:
    """从 `tools/call` 的 `result` 中提取业务 JSON。

    优先 `structuredContent`，否则拼接 `content[].text` 后解析。

    Raises:
        ToolCallError: `isError=true` 或内容为空 / 无法解析。
    """
    if result.get("isError"):
        raise ToolCallError(f"MCP 工具返回错误: {_content_text(result) or '未知错误'}")

    structured = result.get("structuredContent")
    if structured is not None:
        return structured

    text = _content_text(result)
    if not text:
        raise ToolCallError("MCP 工具响应内容为空")
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ToolCallError(f"MCP 工具响应不是合法 JSON: {exc}") from exc


def _content_text(result: dict[str, Any]) -> str:
    """拼接 `content[].text`；没有文本内容时返回空串。"""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    return "\n".join(part for part in parts if part).strip()


def _parse_weather(payload: Any, days: int) -> list[WeatherInfo]:
    """把高德天气响应映射为逐日 `WeatherInfo`。

    Raises:
        ToolCallError: 条数不足 `days` 或必填字段缺失。
    """
    entries = _weather_entries(payload)
    if len(entries) < days:
        raise ToolCallError(f"天气响应不足 {days} 天，实际 {len(entries)} 条")

    today = date.today()
    report: list[WeatherInfo] = []
    try:
        for offset, entry in enumerate(entries[:days]):
            if not isinstance(entry, dict):
                raise ValueError("天气条目不是对象")
            condition = _first_present(entry, _CONDITION_KEYS)
            if not isinstance(condition, str) or not condition.strip():
                raise ValueError("天气条目缺少 condition")
            raw_date = _first_present(entry, _DATE_KEYS)
            target = _parse_date(raw_date) or (today + timedelta(days=offset))
            report.append(
                WeatherInfo(
                    date=target,
                    condition=condition,
                    temp_high=_first_number(_first_present(entry, _HIGH_KEYS)),
                    temp_low=_first_number(_first_present(entry, _LOW_KEYS)),
                )
            )
    except ToolCallError:
        raise
    except Exception as exc:  # 含 pydantic 校验失败
        raise ToolCallError(f"天气响应不符契约: {exc}") from exc
    return report


def _weather_entries(payload: Any) -> list[Any]:
    """把高德天气响应的多种形态统一摊平成逐日条目列表。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ToolCallError("天气响应不是对象或数组")

    casts = payload.get("casts")
    if isinstance(casts, list):
        return casts

    forecasts = payload.get("forecasts")
    if isinstance(forecasts, list):
        flattened: list[Any] = []
        for forecast in forecasts:
            if isinstance(forecast, dict) and isinstance(forecast.get("casts"), list):
                flattened.extend(forecast["casts"])
            else:
                flattened.append(forecast)
        return flattened

    for key in ("days", "data", "weather"):
        entries = payload.get(key)
        if isinstance(entries, list):
            return entries
    if any(key in payload for key in (*_CONDITION_KEYS, "date")):
        return [payload]
    raise ToolCallError("天气响应缺少逐日数据")


def _parse_attractions(payload: Any, city: str, limit: int) -> list[Attraction]:
    """把高德 POI 搜索响应映射为 `Attraction` 列表。

    Raises:
        ToolCallError: `pois` 为空或条目缺少名称。
    """
    pois = _poi_entries(payload)
    if not pois:
        raise ToolCallError("景点搜索结果为空")

    items: list[Attraction] = []
    for poi in pois:
        if not isinstance(poi, dict):
            continue
        name = poi.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        biz_ext = poi.get("biz_ext")
        if not isinstance(biz_ext, dict):
            biz_ext = {}

        ticket = _first_number(
            _first_present(biz_ext, ("cost", "price", "ticket_price"))
            or _first_present(poi, ("ticket_price", "price"))
        )
        rating = _first_number(
            _first_present(biz_ext, ("rating", "score"))
            or _first_present(poi, ("rating", "score"))
        )
        duration = _first_number(
            _first_present(biz_ext, ("duration", "suggested_duration_min"))
            or _first_present(poi, ("suggested_duration_min", "duration"))
        )

        items.append(
            Attraction(
                name=name.strip(),
                city=poi.get("cityname") or city,
                ticket_price=ticket if ticket is not None else _derived_ticket_price(name),
                rating=(
                    rating
                    if rating is not None and 0 <= rating <= 5
                    else _derived_rating(name)
                ),
                suggested_duration_min=(
                    int(duration) if duration is not None and duration >= 0
                    else DEFAULT_SPOT_DURATION_MIN
                ),
            )
        )
        if len(items) >= limit:
            break

    if not items:
        raise ToolCallError("景点搜索结果中没有可用条目")
    return items


def _poi_entries(payload: Any) -> list[Any]:
    """提取 POI 列表，兼容 `pois` / `data` / `results` 三种外壳。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("pois", "data", "results"):
        entries = payload.get(key)
        if isinstance(entries, list):
            return entries
    return []


def _parse_geocodes(payload: Any) -> list[str]:
    """提取 `maps_geo` 响应中的 `"lng,lat"` 坐标列表。"""
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = []
        for key in ("geocodes", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                entries = value
                break
        else:
            entries = [payload]
    else:
        return []

    coordinates: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        location = entry.get("location")
        if isinstance(location, str) and "," in location:
            coordinates.append(location)
            continue
        lng = _first_number(_first_present(entry, ("lng", "lon", "longitude")))
        lat = _first_number(_first_present(entry, ("lat", "latitude")))
        if lng is not None and lat is not None:
            coordinates.append(f"{lng},{lat}")
    return coordinates


def _parse_driving_totals(payload: Any) -> tuple[float, float]:
    """提取驾车路线的 `(distance_m, duration_s)`，无路径时抛 `ToolCallError`。"""
    paths = _route_paths(payload)
    if not paths or not isinstance(paths[0], dict):
        raise ToolCallError("驾车路线响应中没有可用路径")
    path = paths[0]
    distance = _first_number(_first_present(path, ("distance", "distance_m")))
    duration = _first_number(_first_present(path, ("duration", "duration_s")))
    if distance is None:
        raise ToolCallError("驾车路线响应缺少 distance")
    return distance, duration or 0.0


def _route_paths(payload: Any) -> list[Any]:
    """从高德驾车响应的多层外壳中取出 `paths`。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("paths", "route", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested_key in ("paths", "route"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested
            if "distance" in value:
                return [value]
    return []


def _first_number(value: Any) -> float | None:
    """从数字 / 字符串 / 单元素列表中取出第一个数值；无法解析时返回 `None`。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            number = _first_number(item)
            if number is not None:
                return number
        return None
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _stable_index(name: str) -> int:
    """按名称计算稳定的非负整数（sha256，不受 Python 哈希种子影响）。"""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _derived_ticket_price(name: str) -> float:
    """按名称确定性派生门票价（20–160 元，步长 10）。"""
    return DERIVED_TICKET_BASE + (_stable_index(name) % DERIVED_TICKET_RANGE) * DERIVED_TICKET_STEP


def _derived_rating(name: str) -> float:
    """按名称确定性派生评分（3.5–5.0，步长 0.1）。"""
    raw = DERIVED_RATING_BASE + (_stable_index(name) % DERIVED_RATING_RANGE) * DERIVED_RATING_STEP
    return round(min(raw, 5.0), 1)


# ---------------------------------------------------------------- 模块级辅助


def _with_api_key(url: str, api_key: str | None) -> str:
    """URL 未自带 `key` 查询参数时追加 `AMAP_MCP_KEY`。"""
    cleaned = (url or "").strip()
    if not api_key:
        return cleaned
    if "key=" in cleaned:
        return cleaned
    separator = "&" if "?" in cleaned else "?"
    return f"{cleaned}{separator}key={api_key}"


def _iter_sse_messages(text: str) -> Iterator[Any]:
    """解析 SSE 响应体，逐个产出 `data:` 行对应的 JSON 对象。"""
    for block in re.split(r"\r?\n\r?\n", text or ""):
        data_lines = [
            line[len("data:") :].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except ValueError:
            LOGGER.warning("忽略无法解析的 MCP SSE 事件: %r", data[:200])


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """按顺序返回第一个存在且非空的字段值。"""
    for key in keys:
        if key in mapping:
            value = mapping[key]
            if value is not None and value != "":
                return value
    return None


def _parse_date(value: Any) -> date | None:
    """解析 `YYYY-MM-DD` / `YYYY/MM/DD`；无法解析时返回 None。"""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("/", "-")
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        return None


def _rpc_error_message(response: dict[str, Any]) -> str:
    """把 JSON-RPC error 对象转成可读文本。"""
    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message") or "未知错误"
        return f"[{code}] {message}" if code is not None else str(message)
    return str(error or "未知错误")
