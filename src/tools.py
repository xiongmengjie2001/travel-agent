"""ToolCenter：外部工具的注册与调用中心。

是 ReasoningEngine 获取外部信息的唯一通道，内置 weather / attractions /
route / cost 四个工具，并支持后续注册新工具。本模块**不判断**「何时调用
哪个工具」—— 那属于 ReasoningEngine 的职责，ToolCenter 只负责如实调用与
如实抛错。

v3.0 起调用入口 `ToolCenter.call()` 为**异步**：注册表同时容纳同步与异步
工具，同步工具直接执行、异步工具（含返回 awaitable 的同步函数）自动 await。
模块同时导出 `TOOL_DEFINITIONS`（4 个工具的 OpenAI Function Calling 描述），
供 ReasoningEngine 携带给大模型做工具选择。

⚠️ 实现状态：**真实数据源优先 + Mock 降级**（规格见 docs/module-sdd/tools.md §0 与
docs/module-sdd/mcp.md）：

- `weather`：三级链路——高德 MCP（配 `AMAP_MCP_URL`）→ 心知天气 API（配
  `SENIVERSE_API_KEY`）→ 顶部静态表。任一环节未配置或失败自动下沉一级。
- `attractions` / `route`：配置 `AMAP_MCP_URL` 时优先调用高德 MCP，失败回落
  本文件顶部的静态数据；未配置时直接走 Mock，不发起网络请求。
- 所有路径共用同一签名与返回模型，调用方（ReasoningEngine）无感。
- `cost`：始终本地估算，不接外部数据源。
- `validate_destination()`：MCP 地理编码 → Mock 城市白名单 → 判无效。
"""

from __future__ import annotations

import glob
import inspect
import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Callable

import httpx

from src.exceptions import AgentError, ToolCallError
from src.mcp_client import (
    ENV_AMAP_MCP_KEY,
    ENV_AMAP_MCP_URL,
    AmapMCPClient,
)
from src.models import Attraction, CostResult, RouteResult, WeatherInfo

LOGGER = logging.getLogger(__name__)

# ======================================================================
# 城市知识库（第 20 节可选项：48 城真实消费单价 + 景点门票/时长）
# 启动时从 data/cities/<城市>.json 自动加载；文件不存在时保持空 dict，
# 不影响任何既有功能（纯降级到 Mock / 派生函数）。
# ======================================================================

def _load_city_guides() -> dict[str, dict]:
    """扫描 data/cities/**/*.json，全部读入内存。

    支持按「一线 / 新一线 / 二线」分子目录存放（tier1 / tier1_5 / tier2）；
    也兼容直接放在 data/cities/ 根目录的文件。文件名（去 .json）即城市名。

    Returns:
        城市名 → {"cost_per_day": {...}, "spots": [...]} 的字典。
        找不到目录或 JSON 损坏时返回空 dict，不抛异常。
    """
    guides: dict[str, dict] = {}
    base = os.path.join(os.path.dirname(__file__), "..", "data", "cities")
    if not os.path.isdir(base):
        return guides
    for path in sorted(glob.glob(os.path.join(base, "**", "*.json"), recursive=True)):
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
            city = os.path.splitext(os.path.basename(path))[0]
            # 后写的同名文件（比如根目录和 tier 目录重名）不覆盖，先到先得
            guides.setdefault(city, info)
        except Exception as exc:  # noqa: BLE001 — 单文件损坏不影响全库
            LOGGER.warning("跳过损坏的城市知识库 %s: %s", path, exc)
    return guides


CITY_GUIDES: dict[str, dict] = _load_city_guides()
LOGGER.info("已加载 %d 城知识库", len(CITY_GUIDES))


def _lookup_city_spot(city: str, spot_name: str) -> dict | None:
    """按城市 + 景点名查知识库条目，找不到返回 None。"""
    city_info = CITY_GUIDES.get(city)
    if not city_info:
        return None
    for spot in city_info.get("spots", []):
        if spot.get("name") == spot_name:
            return spot
    return None

# ======================================================================
# Mock 数据区 —— 全部假数据集中在此，便于第 20 节整体替换
# ======================================================================

# --- 天气：6 城市 × 7 天静态表 -------------------------------------------------
# 每行 = (天气状况, 最高温℃, 最低温℃)，按 Day1…Day7 排列。
# 取值方式：行下标 = 目标日期的 ordinal % 7。这样 (城市, 日期) → 天气 是一个
# **纯函数** —— 同城市同日期任何时候调用结果都一致（满足数据稳定性要求），
# 且日子每推进一天，取到的就是表中下一行，7 天一轮回。
WEATHER_PATTERN: dict[str, list[tuple[str, float, float]]] = {
    "北京": [
        ("晴", 28.0, 22.0),
        ("多云", 26.0, 21.0),
        ("阴", 25.0, 20.0),
        ("晴", 29.0, 23.0),
        ("多云", 27.0, 22.0),
        ("晴", 30.0, 24.0),
        ("阴", 26.0, 21.0),
    ],
    "上海": [
        ("多云", 30.0, 24.0),
        ("小雨", 28.0, 23.0),
        ("晴", 32.0, 25.0),
        ("多云", 29.0, 24.0),
        ("晴", 31.0, 26.0),
        ("小雨", 27.0, 23.0),
        ("多云", 30.0, 25.0),
    ],
    "杭州": [
        ("晴", 28.0, 23.0),
        ("多云", 27.0, 22.0),
        ("小雨", 26.0, 21.0),
        ("晴", 29.0, 24.0),
        ("多云", 28.0, 23.0),
        ("晴", 30.0, 22.0),
        ("阴", 27.0, 21.0),
    ],
    "成都": [
        ("阴", 25.0, 20.0),
        ("多云", 26.0, 19.0),
        ("小雨", 24.0, 18.0),
        ("阴", 27.0, 20.0),
        ("多云", 28.0, 21.0),
        ("阴", 25.0, 19.0),
        ("小雨", 23.0, 18.0),
    ],
    "广州": [
        ("晴", 33.0, 27.0),
        ("雷阵雨", 31.0, 26.0),
        ("多云", 34.0, 27.0),
        ("晴", 35.0, 28.0),
        ("雷阵雨", 30.0, 25.0),
        ("多云", 33.0, 27.0),
        ("晴", 34.0, 26.0),
    ],
    "深圳": [
        ("多云", 31.0, 26.0),
        ("晴", 33.0, 27.0),
        ("雷阵雨", 29.0, 25.0),
        ("多云", 32.0, 26.0),
        ("晴", 33.0, 27.0),
        ("雷阵雨", 28.0, 24.0),
        ("多云", 30.0, 25.0),
    ],
}

# 未收录城市使用的通用天气模板（温和的晴 / 多云 / 阴 轮换）。
DEFAULT_WEATHER_PATTERN: list[tuple[str, float, float]] = [
    ("晴", 28.0, 20.0),
    ("多云", 27.0, 21.0),
    ("阴", 25.0, 19.0),
    ("晴", 29.0, 22.0),
    ("多云", 26.0, 20.0),
    ("晴", 30.0, 23.0),
    ("阴", 25.0, 19.0),
]

# --- 景点：按城市分组，组内已按热度排序 ----------------------------------------
ATTRACTIONS: dict[str, list[str]] = {
    "北京": ["故宫", "天安门", "长城", "颐和园", "南锣鼓巷", "798艺术区", "天坛"],
    "上海": ["外滩", "东方明珠", "豫园", "南京路", "迪士尼", "田子坊"],
    "杭州": ["西湖", "灵隐寺", "千岛湖", "西溪湿地", "宋城"],
    "成都": ["宽窄巷子", "大熊猫基地", "锦里", "武侯祠", "春熙路"],
    "广州": ["广州塔", "沙面", "陈家祠", "白云山", "长隆"],
    "深圳": ["世界之窗", "欢乐谷", "东部华侨城", "莲花山"],
}

DEFAULT_ATTRACTIONS: list[str] = ["市中心广场", "市政公园", "当地博物馆"]

# Mock 城市白名单：MCP 地理编码不可用 / 未命中时的目的地校验兜底。
MOCK_CITY_WHITELIST: frozenset[str] = frozenset(ATTRACTIONS)

# 景点条目缺失字段的默认值（Mock 数据只给景点名，不含票价）。
DEFAULT_SPOT_DURATION_MIN = 120

# --- 花费：三档单价（元 / 天） -------------------------------------------------
COST_PER_DAY_BUDGET = 300.0
COST_PER_DAY_MEDIUM = 600.0
COST_PER_DAY_LUXURY = 1200.0
DEFAULT_COST_PER_DAY = COST_PER_DAY_MEDIUM  # 未提供预算时的默认档

# 总花费的内部拆分比例（门票 = 总额 - 市内交通 - 住宿 - 餐饮，保证四项之和
# 恰好等于总额）。v3.0 起由三段升级为四段分项。
LODGING_RATIO = 0.45
FOOD_RATIO = 0.25
TRANSPORT_RATIO = 0.15

# --- 路线 ---------------------------------------------------------------------
# 空景点列表时抛错（见 docs/module-sdd/tools.md §4）；有景点时按输入顺序串联。
TRANSIT_MIN_PER_HOP = 30  # 相邻两点之间的在途时长（分钟）
TRANSIT_KM_PER_HOP = 5.0  # 相邻两点之间的行驶距离（公里，公共交通）
WALKING_KM_PER_HOP = 1.5  # 相邻两点之间的行驶距离（公里，步行）
TRANSPORT_WALKING = "步行"
TRANSPORT_TRANSIT = "地铁 + 步行"
WALKING_MAX_SPOTS = 2  # 不超过该数量时推荐全程步行

# 天数上限，与 TripPlan.days 的 le=30 保持一致。
MAX_FORECAST_DAYS = 30

# --- Function Calling 工具描述 -------------------------------------------------
# OpenAI / DeepSeek 兼容的 tools 参数格式。工具名与 ToolCenter 注册表**严格
# 一致**（weather / attractions / route / cost），ReasoningEngine 依此把模型
# 返回的 tool_calls 直接交给 `ToolCenter.call()`，无需再做名称映射。
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "attractions",
            "description": (
                "查询指定城市的景点列表，按热度排序。规划行程前必须先调用本工具，"
                "禁止凭空编造景点。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 北京"},
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 10",
                        "minimum": 1,
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": (
                "查询指定城市自今天起逐日的天气。生成行程时必须调用，禁止编造天气。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 北京"},
                    "days": {
                        "type": "integer",
                        "description": "需要的天数，从今天算起",
                        "minimum": 1,
                        "maximum": 30,
                    },
                },
                "required": ["city", "days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route",
            "description": (
                "计算景点之间的游览顺序、交通方式与总行驶距离。传入选定景点后可"
                "获得当日路线。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 北京"},
                    "spots": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "景点名列表，非空，顺序即游览顺序",
                        "minItems": 1,
                    },
                },
                "required": ["city", "spots"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cost",
            "description": (
                "估算行程花费，返回门票 / 市内交通 / 住宿 / 餐饮四段分项及总计。"
                "生成行程时应调用，以便给出真实预算。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "行程天数",
                        "minimum": 1,
                        "maximum": 30,
                    },
                    "spots": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "景点名列表",
                    },
                    "budget": {
                        "anyOf": [{"type": "number"}, {"type": "null"}],
                        "description": "用户预算（元），未提供时为 null",
                    },
                },
                "required": ["days", "spots"],
            },
        },
    },
]


class ToolCenter:
    """工具注册表。实例化时自动注册 4 个内置工具。

    v4.0 起持有可选的 `AmapMCPClient`：`weather` / `attractions` / `route`
    优先走高德 MCP，失败或未配置时回落模块底部的 Mock 实现；`cost` 始终本地。
    """

    def __init__(self, mcp_client: AmapMCPClient | None = None) -> None:
        """初始化注册表，并自动注册 weather / attractions / route / cost。

        Args:
            mcp_client: 高德 MCP 客户端。为 `None` 时按环境变量
                `AMAP_MCP_URL` / `AMAP_MCP_KEY` 自动构建；显式传入实例时
                作为测试注入点。需要「强制纯 Mock」的测试清空环境变量即可。
        """
        self._mcp = mcp_client if mcp_client is not None else _build_mcp_client_from_env()
        self._tools: dict[str, Callable[..., Any]] = {}
        self.register("weather", self._weather_tool)
        self.register("attractions", self._attractions_tool)
        self.register("route", self._route_tool)
        self.register("cost", _cost)

    def register(self, name: str, func: Callable[..., Any]) -> None:
        """注册一个工具函数。

        Args:
            name: 工具名，全局唯一。
            func: 工具实现，必须带完整的参数与返回类型标注。

        Raises:
            ToolCallError: name 已存在。
        """
        if name in self._tools:
            raise ToolCallError(f"工具 '{name}' 已注册，不能重复注册")
        self._tools[name] = func

    async def call(self, name: str, **params: Any) -> Any:
        """按名调用已注册的工具（统一异步入口）。

        注册表同时容纳同步与异步工具：同步工具直接执行；返回值为 awaitable
        的（协程函数、或同步函数返回了协程）一律 `await`。调用方**始终 await
        本方法**，无需关心具体工具的实现方式。

        Args:
            name: 工具名。
            **params: 按该工具签名展开的关键字实参。这是 CLAUDE.md 已确认的
                裸 dict 例外之一，仅限本入口。

        Returns:
            工具返回值，必须是 Pydantic 模型或模型列表。

        Raises:
            ToolCallError: 工具不存在、参数与签名不匹配，或执行过程出错。
        """
        if name not in self._tools:
            raise ToolCallError(f"未注册的工具: {name}")

        func = self._tools[name]
        # 同步与异步在此分流：调用期就抛 TypeError 才是「签名不匹配」；
        # 协程体内部的 TypeError 应归入下面的「执行失败」。
        try:
            result = func(**params)
        except TypeError as exc:
            raise ToolCallError(f"工具 '{name}' 的参数与签名不匹配: {exc}") from exc
        except AgentError:
            raise
        except Exception as exc:
            # 同步工具在**调用期**就执行完函数体，异常在此浮出。
            raise ToolCallError(f"工具 '{name}' 执行失败: {exc}") from exc

        try:
            if inspect.isawaitable(result):
                return await result
            return result
        except AgentError:
            # 工具内部已抛出规范化异常（含 ToolCallError），保留原始 msg 与
            # __cause__，避免二次包装把有效信息覆盖掉。
            raise
        except Exception as exc:
            raise ToolCallError(f"工具 '{name}' 执行失败: {exc}") from exc

    def list_tools(self) -> list[str]:
        """返回已注册工具名的列表（副本，调用方修改不影响注册表）。"""
        return list(self._tools)

    async def validate_destination(self, city: str) -> bool:
        """校验目的地是否有效。

        顺序（见 docs/module-sdd/mcp.md §4.2）：

        1. 空字符串 / 纯空白 → `False`；
        2. MCP 已配置 → `AmapMCPClient.city_exists()`（内部不抛异常）；
        3. 未命中 → 查 `MOCK_CITY_WHITELIST`；
        4. 仍未命中 → `False`。

        Args:
            city: 待校验的城市名。

        Returns:
            有效返回 `True`，否则 `False`。**不抛异常**。
        """
        cleaned = (city or "").strip()
        if not cleaned:
            return False
        if self._mcp is not None:
            try:
                if await self._mcp.city_exists(cleaned):
                    return True
            except Exception as exc:  # noqa: BLE001 —— 校验失败一律回落白名单
                LOGGER.warning("MCP 目的地校验异常，回落 Mock 白名单: %s", exc)
        return cleaned in MOCK_CITY_WHITELIST

    async def close(self) -> None:
        """关闭内部 MCP 客户端；未配置时为空操作，可重复调用。"""
        if self._mcp is None:
            return
        try:
            await self._mcp.close()
        except Exception as exc:  # noqa: BLE001 —— 关闭失败不应影响调用方
            LOGGER.warning("关闭 MCP 客户端失败（忽略）: %s", exc)

    # ------------------------------------------------------------ MCP 优先包装

    async def _weather_tool(self, city: str, days: int) -> list[WeatherInfo]:
        """weather 工具：MCP → 心知 → Mock 三级降级。

        Raises:
            ToolCallError: days 越界；全部数据源均失败。
        """
        _validate_days(days)
        if self._mcp is not None:
            try:
                return await self._mcp.weather(city, days)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("MCP 天气调用失败，尝试心知: %s", exc)
        seniverse = await _weather_from_seniverse(city, days)
        if seniverse is not None:
            return seniverse
        return _weather_from_mock(city, days)

    async def _attractions_tool(self, city: str, limit: int = 10) -> list[Attraction]:
        """attractions 工具：MCP 优先，失败降级 Mock；知识库覆盖门票/时长。

        Raises:
            ToolCallError: limit < 1；MCP 与 Mock 均失败。
        """
        if limit < 1:
            raise ToolCallError(f"景点条数上限必须 ≥ 1，收到 {limit}")
        raw: list[Attraction]
        if self._mcp is not None:
            try:
                raw = await self._mcp.attractions(city, limit)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("MCP 景点调用失败，降级 Mock: %s", exc)
                raw = _attractions(city, limit)
        else:
            raw = _attractions(city, limit)

        # --- 知识库覆盖：门票 / 时长 ---
        if city in CITY_GUIDES:
            for a in raw:
                spot_info = _lookup_city_spot(city, a.name)
                if spot_info:
                    a.ticket_price = float(spot_info.get("ticket", a.ticket_price or 0))
                    a.suggested_duration_min = int(spot_info.get("duration_min", a.suggested_duration_min))
        return raw

    async def _route_tool(self, city: str, spots: list[str]) -> RouteResult:
        """route 工具：MCP 优先，失败降级 Mock。

        Raises:
            ToolCallError: spots 为空；MCP 与 Mock 均失败。
        """
        if not spots:
            raise ToolCallError("景点列表为空，无法规划路线")
        if self._mcp is not None:
            try:
                return await self._mcp.route(city, spots)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("MCP 路线调用失败，降级 Mock: %s", exc)
        return _route(city, spots)


def _build_mcp_client_from_env() -> AmapMCPClient | None:
    """按环境变量构建高德 MCP 客户端。

    Returns:
        `AMAP_MCP_URL` 有值时返回客户端（`AMAP_MCP_KEY` 可为空）；
        未配置时返回 `None`，表示纯 Mock 模式。
    """
    url = (os.getenv(ENV_AMAP_MCP_URL) or "").strip()
    if not url:
        return None
    api_key = (os.getenv(ENV_AMAP_MCP_KEY) or "").strip() or None
    return AmapMCPClient(url, api_key)


async def _weather(city: str, days: int) -> list[WeatherInfo]:
    """查询逐日天气（Mock 数据源）。

    v4.0 起 MCP 优先逻辑上移到 `ToolCenter._weather_tool()`；本函数只保留
    静态表实现，既是降级路径，也供测试直接验证 Mock 契约。

    Args:
        city: 城市名。
        days: 需要的天数，从今天算起。

    Returns:
        长度为 `days` 的逐日天气，日期自今天起连续排列。

    Raises:
        ToolCallError: days 越界（<1 或 >30）。
    """
    _validate_days(days)
    return _weather_from_mock(city, days)


def _validate_days(days: int) -> None:
    """校验天气天数落在 1–30。

    Raises:
        ToolCallError: 越界。
    """
    if days < 1:
        raise ToolCallError(f"天气查询天数必须 ≥ 1，收到 {days}")
    if days > MAX_FORECAST_DAYS:
        raise ToolCallError(f"天气查询天数不得超过 {MAX_FORECAST_DAYS}，收到 {days}")


# --- 心知天气（真实数据源） ----------------------------------------------------
# v3 每日预报接口：https://api.seniverse.com/v3/weather/daily.json
# 免费版上限 3 天预报，超出部分由 Mock 补齐；任一失败由调用方降级 Mock。
ENV_SENIVERSE_API_KEY = "SENIVERSE_API_KEY"
SENIVERSE_DAILY_URL = "https://api.seniverse.com/v3/weather/daily.json"
SENIVERSE_TIMEOUT = 10.0
SENIVERSE_MAX_DAYS = 3  # 免费版预报天数上限


async def _weather_from_seniverse(city: str, days: int) -> list[WeatherInfo] | None:
    """心知天气数据源（v3 每日预报）。

    未配置 `SENIVERSE_API_KEY` 或调用失败（网络 / 超时 / Key 无效 / 响应异常）
    时返回 None，由调用方降级 Mock，本函数**不抛异常**。免费版最多返回
    `SENIVERSE_MAX_DAYS` 天；请求天数更多时，超出部分用 Mock 数据补齐，
    保证返回长度恒为 `days`、日期自今天起连续。

    Returns:
        长度为 `days` 的逐日天气；未配置 Key 或调用失败时为 None。
    """
    api_key = (os.getenv(ENV_SENIVERSE_API_KEY) or "").strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=SENIVERSE_TIMEOUT) as client:
            response = await client.get(
                SENIVERSE_DAILY_URL,
                params={
                    "key": api_key,
                    "location": city,
                    "language": "zh-Hans",
                    "unit": "c",
                    "start": 0,
                    "days": min(days, SENIVERSE_MAX_DAYS),
                },
            )
            response.raise_for_status()
            payload = response.json()
        report = [
            WeatherInfo(
                date=date.fromisoformat(item["date"]),
                condition=item["text_day"],
                temp_high=float(item["high"]),
                temp_low=float(item["low"]),
            )
            for item in payload["results"][0]["daily"]
        ]
    except Exception as exc:  # noqa: BLE001 —— 任何失败都降级，不中断链路
        LOGGER.warning("心知天气调用失败，降级 Mock: %s", exc)
        return None
    if len(report) < days:  # 免费版天数不足：Mock 补齐，日期正好衔接
        report.extend(_weather_from_mock(city, days)[len(report):])
    return report


def _weather_from_mock(city: str, days: int) -> list[WeatherInfo]:
    """静态表数据源。

    行下标 = `日期.toordinal() % 7`，因此**同城市同日期返回值恒定**，且日期每
    推进一天取表中下一行，7 天一轮回。未收录城市回退 `DEFAULT_WEATHER_PATTERN`。
    """
    pattern = WEATHER_PATTERN.get(city, DEFAULT_WEATHER_PATTERN)
    today = date.today()
    report: list[WeatherInfo] = []
    for offset in range(days):
        target = today + timedelta(days=offset)
        condition, temp_high, temp_low = pattern[target.toordinal() % len(pattern)]
        report.append(
            WeatherInfo(
                date=target,
                condition=condition,
                temp_high=temp_high,
                temp_low=temp_low,
            )
        )
    return report


def _attractions(city: str, limit: int = 10) -> list[Attraction]:
    """查询城市景点列表（Mock：读 ATTRACTIONS 静态表）。

    组内已按热度排序，取前 `limit` 条；未收录城市回退 `DEFAULT_ATTRACTIONS`。

    Args:
        city: 城市名。
        limit: 返回条数上限，必须 ≥ 1。

    Returns:
        按热度排列的景点列表。

    Raises:
        ToolCallError: limit < 1。
    """
    if limit < 1:
        raise ToolCallError(f"景点条数上限必须 ≥ 1，收到 {limit}")

    names = ATTRACTIONS.get(city, DEFAULT_ATTRACTIONS)
    return [
        Attraction(
            name=name,
            city=city,
            ticket_price=None,  # Mock 数据不含票价，留空由消费方估算
            suggested_duration_min=DEFAULT_SPOT_DURATION_MIN,
        )
        for name in names[:limit]
    ]


def _route(city: str, spots: list[str]) -> RouteResult:
    """计算景点间的游览顺序与交通方式（本地计算，无外部依赖）。

    严格保持 `spots` 的输入顺序 —— 消费方不得重排（对应 AC-1 的路线顺序）。

    Args:
        city: 城市名。
        spots: 景点名列表，非空。

    Returns:
        RouteResult；`ordered_spots` 与输入等长同序。

    Raises:
        ToolCallError: spots 为空。
    """
    if not spots:
        raise ToolCallError("景点列表为空，无法规划路线")

    hops = len(spots) - 1
    walking = len(spots) <= WALKING_MAX_SPOTS
    transport = TRANSPORT_WALKING if walking else TRANSPORT_TRANSIT
    km_per_hop = WALKING_KM_PER_HOP if walking else TRANSIT_KM_PER_HOP
    return RouteResult(
        city=city,
        ordered_spots=list(spots),
        transport=transport,
        total_travel_min=hops * TRANSIT_MIN_PER_HOP,
        total_distance_km=round(hops * km_per_hop, 2),
    )


def _cost(
    days: int,
    spots: list[str],
    budget: float | None = None,
    city: str | None = None,
) -> CostResult:
    """估算行程花费（本地计算，无外部依赖）。

    知识库路径（优先）：`data/cities/<城市>.json` 里的 `cost_per_day`
    直接给出 lodging / food / transit 每日单价，门票按景点名查知识库累加。
    未命中知识库时回退到以下估算逻辑：

    单价按预算反推档位：`budget / days` 低于 600 元取 300 元档，600–1200 元取
    600 元档，高于 1200 元取 1200 元档；未提供预算时取默认的 600 元档。
    `total = days × 单价`，再按住宿 45% / 餐饮 25% / 市内交通 15% / 门票余额
    拆分为四段分项。

    Args:
        days: 行程天数，必须 ≥ 1。
        spots: 景点名列表，当前仅作记录。
        budget: 用户预算（元）；为 None 时 `within_budget` 返回 None。
        city: 城市名；用于查知识库真实单价与门票。

    Returns:
        CostResult。

    Raises:
        ToolCallError: days < 1。
    """
    if days < 1:
        raise ToolCallError(f"行程天数必须 ≥ 1，收到 {days}")

    # --- 知识库路径 ---
    if city and city in CITY_GUIDES:
        cp = CITY_GUIDES[city].get("cost_per_day", {})
        lodging_daily = cp.get("lodging")
        food_daily = cp.get("food")
        transit_daily = cp.get("transit")
        if all(isinstance(v, (int, float)) for v in (lodging_daily, food_daily, transit_daily)):
            lodging = round(lodging_daily * days, 2)
            food = round(food_daily * days, 2)
            transport = round(transit_daily * days, 2)
            # 门票：累加知识库已知景点，未知的按单景点 0 算（不再凭空派生）
            tickets = 0.0
            for name in spots:
                spot_info = _lookup_city_spot(city, name)
                if spot_info:
                    tickets += float(spot_info.get("ticket", 0))
            tickets = round(tickets, 2)
            total = round(lodging + food + transport + tickets, 2)
            LOGGER.info("知识库成本估算 [%s×%d天] 住宿%.0f+餐饮%.0f+交通%.0f+门票%.0f=%.0f",
                        city, days, lodging, food, transport, tickets, total)
            return CostResult(
                days=days,
                tickets_total=tickets,
                transport_total=transport,
                lodging_total=lodging,
                food_total=food,
                total=total,
                within_budget=None if budget is None else total <= budget,
            )

    # --- 原有档位估算（保持不变） ---
    per_day = _pick_cost_per_day(days, budget)
    total = round(days * per_day, 2)
    lodging = round(total * LODGING_RATIO, 2)
    food = round(total * FOOD_RATIO, 2)
    transport = round(total * TRANSPORT_RATIO, 2)
    tickets = round(total - lodging - food - transport, 2)
    return CostResult(
        days=days,
        tickets_total=tickets,
        transport_total=transport,
        lodging_total=lodging,
        food_total=food,
        total=total,
        within_budget=None if budget is None else total <= budget,
    )


def _pick_cost_per_day(days: int, budget: float | None) -> float:
    """按人均日预算反推花费档位单价。

    Args:
        days: 行程天数，已由调用方保证 ≥ 1。
        budget: 用户预算（元）；为 None 时返回 `DEFAULT_COST_PER_DAY`。

    Returns:
        每天的单价（元）。
    """
    if budget is None:
        return DEFAULT_COST_PER_DAY
    per_day_budget = budget / days
    if per_day_budget < COST_PER_DAY_MEDIUM:
        return COST_PER_DAY_BUDGET
    if per_day_budget <= COST_PER_DAY_LUXURY:
        return COST_PER_DAY_MEDIUM
    return COST_PER_DAY_LUXURY
