"""ReasoningEngine：意图解析与行程生成。

把用户自然语言需求解析为结构化 Intent，并据此生成完整的 TripPlan。
优先通过 DeepSeek API 完成，调用失败时降级为本地模板方案，保证接口
始终可用（对应 AC-5）。本模块不读写数据库，也不做 API 格式转换。

DeepSeek 走本模块**直连**，不经由 ToolCenter —— ToolCenter 管理的是可注册
的业务工具，LLM 推理通路不属于工具调用语义。

v3.0 起 AI 路径改为 **Function Calling 多轮工具调用**：`generate_itinerary`
首次请求即携带 `TOOL_DEFINITIONS`，模型自主决定调用景点 / 天气 / 路线 /
花费工具；每轮工具结果以 `role=tool` 消息回传，最多 3 轮，之后融合生成
行程。模板路径与全部降级规则保持不变，且全链路（工具中心 → 推理引擎 →
调度器 → 路由）为异步。

实现说明：

1. DeepSeek 经 **httpx** 直连 OpenAI 兼容的 `/chat/completions`，超时 30s；
   不引入 `openai` SDK，保持离线可跑性。
2. `_call_deepseek` 返回 assistant message（dict）而非纯文本 —— 需同时承载
   `content` 与 `tool_calls`；失败以 `None` 表达（SDD §4 要求不向外抛出）。

规格见 docs/module-sdd/reasoning.md。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from src.exceptions import (
    AgentError,
    DestinationInvalidError,
    IntentUnrecognizedError,
    LLMUnavailableError,
    ToolCallError,
)
from src.models import (
    Attraction,
    CostResult,
    DailyPlan,
    Intent,
    Spot,
    TripPlan,
    WeatherInfo,
)
from src.tools import TOOL_DEFINITIONS, ToolCenter

DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # OpenAI 兼容接口
DEFAULT_MODEL = "deepseek-v4-flash"
ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"
ENV_USE_MOCK_LLM = "USE_MOCK_LLM"
DEFAULT_HTTP_TIMEOUT = 30.0
MAX_TOOL_ROUNDS = 3  # Function Calling 最多轮数（每轮模型可发起一批工具调用）

# `_env_flag` 认可的真值写法（去空白、忽略大小写后比较）；其余一律为假。
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

DEFAULT_TEMPLATE_DAYS = 3  # 未解析出天数时的默认行程天数
DEFAULT_TEMPLATE_COST_PER_DAY = 600.0  # cost 工具失败时的本地估算单价（元/天）
DEFAULT_TEMPLATE_SPOT_DURATION_MIN = 120

# 预设城市列表 —— 命中即视为目的地。与 docs/module-sdd/tools.md §0 的 Mock
# 景点池覆盖范围一致；未命中的城市仍可经「去 X」句式由正则提取。
KNOWN_CITIES: tuple[str, ...] = ("北京", "上海", "杭州", "成都", "广州", "深圳")

_DAYS_PATTERN = re.compile(r"(\d+)\s*(?:天|日)")
_BUDGET_PATTERN = re.compile(r"(\d{3,5})")
_BUDGET_HINTS = ("预算", "费用", "花费", "经费", "元", "块", "以内", "左右")
_BUDGET_HINT_WINDOW = 6
_MAX_DAYS = 30  # 与 TripPlan.days / Intent.days 的 le=30 对齐

# 出发日期：相对词优先于「X 月 Y 日」，ISO 日期优先于两者。
_RELATIVE_DATE_OFFSETS: dict[str, int] = {
    "大后天": 3,
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
}
_ISO_DATE_PATTERN = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?")
_MONTH_DAY_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
DEFAULT_START_OFFSET_DAYS = 1  # 未指定时默认明天出发

# 未命中预设城市时，从「去 / 到 / 前往 …」句式里提取目的地。
_DESTINATION_PATTERN = re.compile(r"(?:去|到|前往|想去|要去)\s*([一-龥]{2,4})")
# 「地名 + N 天/日」句式，如「西安4日游」「三亚三天」。
_DESTINATION_DAYS_PATTERN = re.compile(
    r"([一-龥]{2,4})\s*(?:\d+|[一二三四五六七八九十两]+)\s*(?:天|日)"
)
# 目的地候选词中一旦出现这些字即截断，避免把「西安玩三天」整段当成城市名。
# 截断只在第 3 个字之后生效 —— 否则「天津」会被首字「天」误伤。
_DESTINATION_NOISE = "天日游玩的旅行旅游吃喝住价格元块预算费"
# 候选词可能带上的动词 / 填充前缀，由长到短剥离。
_DESTINATION_PREFIXES = (
    "帮我",
    "请问",
    "我想",
    "规划",
    "安排",
    "想去",
    "要去",
    "前往",
    "出发",
    "请",
    "去",
    "到",
    "在",
    "从",
)

# 偏好标签 → 触发关键词。
PREFERENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "美食": ("美食", "小吃", "餐厅", "吃"),
    "购物": ("购物", "逛街", "买买买"),
    "文化": ("文化", "历史", "古迹", "博物馆"),
    "亲子": ("亲子", "孩子", "带娃", "家庭"),
    "自然": ("自然", "风景", "山水", "户外", "爬山"),
}

# 模板方案的内置景点池 —— 与 tools 的 Mock 池**故意重复**：SDD §2 要求
# attractions 工具失败时回退到本池，回退源若与被回退对象同源就失去了意义。
TEMPLATE_SPOTS: dict[str, list[str]] = {
    "北京": ["故宫", "天安门", "长城", "颐和园", "南锣鼓巷", "798艺术区", "天坛"],
    "上海": ["外滩", "东方明珠", "豫园", "南京路", "迪士尼", "田子坊"],
    "杭州": ["西湖", "灵隐寺", "千岛湖", "西溪湿地", "宋城"],
    "成都": ["宽窄巷子", "大熊猫基地", "锦里", "武侯祠", "春熙路"],
    "广州": ["广州塔", "沙面", "陈家祠", "白云山", "长隆"],
    "深圳": ["世界之窗", "欢乐谷", "东部华侨城", "莲花山"],
}
DEFAULT_TEMPLATE_SPOTS: list[str] = ["市中心广场", "市政公园", "当地博物馆"]

_LLM_SCHEMA_HINT = (
    '{"destination": 城市名, "days": 天数, "budget": 预算或null, "total_cost": 总花费,'
    ' "daily": [{"day_index": 第几天, "date": "YYYY-MM-DD"或null,'
    ' "spots": [{"name": 景点名, "duration_min": 分钟数, "ticket_price": 门票元数}],'
    ' "estimated_cost": 当日花费,'
    ' "weather": {"date": "YYYY-MM-DD", "condition": 天气,'
    ' "temp_high": 最高温, "temp_low": 最低温}或null}],'
    ' "degraded": false}'
)

# AI 路径的系统提示词：约束模型先调工具、再生成，且最终只输出标准 JSON。
AI_SYSTEM_PROMPT = (
    "你是旅行规划智能体，必须严格遵守以下规则：\n"
    "1. 先调用工具获取真实数据：用 attractions 查景点、weather 查天气、"
    "route 排路线、cost 估花费；禁止编造景点、天气或价格。\n"
    "2. 至少调用 attractions 与 weather 之后再生成行程；拿到工具结果后，"
    "基于真实数据编排每日景点与日期。\n"
    "3. 工具失败时如实标注或改用其他工具，不得编造结果。\n"
    "4. 最终只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字。\n"
    f"5. JSON 字段与类型：{_LLM_SCHEMA_HINT}\n"
)

LOGGER = logging.getLogger(__name__)


class ReasoningEngine:
    """意图解析 + 行程生成，内部完成全部降级。"""

    def __init__(self, tool_center: ToolCenter, api_key: str | None = None) -> None:
        """注入工具中心。

        Args:
            tool_center: 外部信息来源（天气 / 景点 / 路线 / 花费）。
            api_key: DeepSeek API Key。为 None 时从环境变量
                `DEEPSEEK_API_KEY` 读取；仍为空则进入纯模板模式。
                **禁止硬编码**（CLAUDE.md 禁止事项）。

        环境变量 `USE_MOCK_LLM` 为真时**强制不出网**，即使提供了 `api_key` 也
        只走模板方案（见 `_call_deepseek`）。读取时机是构造期 —— 运行中改环境
        变量不会生效，须重启进程。
        """
        self._tools = tool_center
        self._mock_llm = _env_flag(ENV_USE_MOCK_LLM)
        self._api_key = api_key if api_key is not None else os.getenv(ENV_API_KEY)
        self._base_url = os.getenv(ENV_BASE_URL, DEEPSEEK_BASE_URL).rstrip("/")
        self._model = os.getenv(ENV_MODEL, DEFAULT_MODEL)
        self._timeout = DEFAULT_HTTP_TIMEOUT

    # ------------------------------------------------------------ 意图解析

    def parse_intent(self, text: str, start_date: date | None = None) -> Intent:
        """解析目的地 / 天数 / 出发日期 / 预算 / 偏好。

        天数优先于预算匹配：先定位「N 天」并从文本中挖空该片段，再在剩余文本
        里找 3 位以上的数字作为预算，避免把天数误当成预算；日期片段同样先挖空，
        避免把年份 / 月日误当成预算。

        出发日期优先级：调用方传入的 `start_date` > 文本中的日期 > 明天。

        Args:
            text: 用户原始输入。
            start_date: 前端显式传入的出发日期；为 None 时从文本解析。

        Returns:
            Intent；`start_date` 解析后恒有值，其余未解析出的字段为 None。

        Raises:
            IntentUnrecognizedError: 无法从文本中识别出目的地。
        """
        raw = (text or "").strip()
        if not raw:
            raise IntentUnrecognizedError("输入为空，无法识别目的地")

        destination = self._extract_destination(raw)
        if not destination:
            raise IntentUnrecognizedError("无法从输入中识别目的地，请补充后重试")

        days_match = _DAYS_PATTERN.search(raw)
        days = _safe_days(days_match.group(1)) if days_match else None

        date_match, parsed_date = _match_date(raw)
        resolved_start = start_date or parsed_date or _default_start_date()

        remaining = raw
        if days_match:
            remaining = _mask_span(remaining, days_match)
        if date_match:
            remaining = _mask_span(remaining, date_match)
        budget = _extract_budget(remaining)
        preferences = _extract_preferences(raw)

        return Intent(
            raw_text=raw,
            destination=destination,
            days=days,
            start_date=resolved_start,
            budget=budget,
            preferences=preferences,
            confidence=_score_confidence(days, budget, preferences),
        )

    def _extract_destination(self, text: str) -> str | None:
        """先匹配预设城市，再退回「去 / 到 / 前往 X」与「X + N 天」句式。

        Returns:
            城市名；均未命中时为 None。
        """
        hit = _find_earliest(text, KNOWN_CITIES)
        if hit:
            return hit

        match = _DESTINATION_PATTERN.search(text)
        if match:
            candidate = _clean_destination(match.group(1))
            if candidate:
                return candidate

        for match in _DESTINATION_DAYS_PATTERN.finditer(text):
            candidate = _clean_destination(match.group(1))
            if candidate:
                return candidate
        return None

    async def validate_destination(self, city: str) -> bool:
        """校验目的地是否有效（委托 ToolCenter）。

        工具中心按「MCP 地理编码 → Mock 城市白名单」的顺序判断，且本方法
        不抛异常。测试替身若未实现该校验，则视为通过，避免把「未实现」误判
        为「目的地无效」。
        """
        validator = getattr(self._tools, "validate_destination", None)
        if validator is None:
            return True
        return bool(await validator(city))

    # ------------------------------------------------------------ 行程生成

    async def generate_itinerary(self, intent: Intent) -> TripPlan:
        """生成行程方案（异步）。

        AI 路径采用 **Function Calling 多轮循环**：模型自主决定调用哪些工具、
        多轮获取真实数据后再输出行程；工具结果原样回传给模型参与编排。AI 失败
        或轮数耗尽时降级为模板方案。**单点失败不抛异常**。

        Raises:
            IntentUnrecognizedError: `intent.destination` 为空。
            DestinationInvalidError: 目的地未通过有效性校验。
            LLMUnavailableError: DeepSeek 与模板方案**同时**不可用。
        """
        if not intent.destination:
            raise IntentUnrecognizedError("无法从输入中识别目的地，请补充后重试")
        if not await self.validate_destination(intent.destination):
            raise DestinationInvalidError(f"未找到目的地「{intent.destination}」")

        if self._api_key:
            plan = await self._generate_with_tools(intent)
            if plan is not None:
                return plan
            # Mock 模式下这里必然拿不到 LLM 产出，且这是被要求的路径 ——
            # 再打「不可用」会把用户主动选择的离线模式报成故障，误导排查。
            if not self._mock_llm:
                LOGGER.warning("DeepSeek 不可用或响应无法解析，降级为模板方案")

        try:
            return await self.generate_template(intent)
        except AgentError:
            raise
        except Exception as exc:  # 模板方案失败属理论路径，兜底为 2001
            raise LLMUnavailableError("行程生成失败：LLM 与模板方案均不可用") from exc

    async def generate_template(self, intent: Intent) -> TripPlan:
        """本地模板方案，恒定标记 degraded=True。

        产出必须独立满足 AC-1 的结构要求：每日景点列表、路线顺序、
        预估花费、天气信息。天气仍尽力经 ToolCenter 获取；单个工具失败
        只影响对应字段（SDD §2「工具失败的粒度」），不阻断本次生成。

        Raises:
            IntentUnrecognizedError: `intent.destination` 为空。
        """
        destination = intent.destination
        if not destination:
            raise IntentUnrecognizedError("模板方案需要目的地，但 intent 中为空")

        days = intent.days or DEFAULT_TEMPLATE_DAYS
        attractions = await self._fetch_attractions(destination)
        buckets = _distribute(attractions, days)
        start = _intent_start_date(intent)
        weather = await self._fetch_weather_aligned(destination, start, days)
        cost = await self._fetch_cost(intent, days, [item.name for item in attractions])

        daily: list[DailyPlan] = []
        for offset in range(days):
            day_spots = await self._order_spots(destination, buckets[offset])
            daily.append(
                DailyPlan(
                    day_index=offset + 1,
                    date=start + timedelta(days=offset),
                    spots=_to_spots(day_spots),
                    estimated_cost=_split_day_cost(cost.total, days, offset),
                    weather=weather[offset] if offset < len(weather) else None,
                )
            )

        return TripPlan(
            destination=destination,
            days=days,
            budget=intent.budget,
            total_cost=cost.total,
            daily=daily,
            degraded=True,
            weather_report=weather,
            cost_breakdown=cost,
            source="template",
        )

    # ------------------------------------------------------------ 工具适配

    async def _fetch_attractions(self, destination: str) -> list[Attraction]:
        """取景点；工具失败时回退到 `TEMPLATE_SPOTS` 内置池。"""
        try:
            return await self._tools.call("attractions", city=destination, limit=10)
        except ToolCallError as exc:
            LOGGER.warning("景点工具失败，回退内置池: %s", exc)
            names = TEMPLATE_SPOTS.get(destination, DEFAULT_TEMPLATE_SPOTS)
            return [Attraction(name=name, city=destination) for name in names]

    async def _fetch_weather(self, destination: str, days: int) -> list[WeatherInfo]:
        """取天气；工具失败时返回空列表（调用方据此把 weather 置 None）。"""
        try:
            return await self._tools.call("weather", city=destination, days=days)
        except ToolCallError as exc:
            LOGGER.warning("天气工具失败，天气字段置空: %s", exc)
            return []

    async def _fetch_weather_aligned(
        self, destination: str, start: date, days: int
    ) -> list[WeatherInfo]:
        """取与出发日期对齐的天气。

        天气工具始终从**今天**开始返回连续日期；用户指定未来出发日期时，
        先多取「出发日 − 今天」天，再切片出与行程对应的那一段。天气接口上限
        30 天，超出部分留空，不影响行程生成。
        """
        offset = max((start - date.today()).days, 0)
        fetch_days = min(days + offset, _MAX_DAYS)
        if fetch_days < 1:
            return []
        report = await self._fetch_weather(destination, fetch_days)
        return report[offset : offset + days]

    async def _order_spots(
        self, destination: str, items: list[Attraction]
    ) -> list[Attraction]:
        """按 route 工具给出的顺序重排当日景点；工具失败时保持原顺序。"""
        if len(items) < 2:
            return items
        try:
            result = await self._tools.call(
                "route", city=destination, spots=[item.name for item in items]
            )
        except ToolCallError as exc:
            LOGGER.warning("路线工具失败，沿用原顺序: %s", exc)
            return items

        by_name = {item.name: item for item in items}
        ordered = [by_name[name] for name in result.ordered_spots if name in by_name]
        ordered_names = {item.name for item in ordered}
        ordered.extend(item for item in items if item.name not in ordered_names)
        return ordered or items

    async def _fetch_cost(
        self, intent: Intent, days: int, spots: list[str]
    ) -> CostResult:
        """取花费估算；工具失败时按本地单价估算。"""
        try:
            return await self._tools.call(
                "cost", days=days, spots=spots, budget=intent.budget, city=intent.destination
            )
        except ToolCallError as exc:
            LOGGER.warning("花费工具失败，按本地单价估算: %s", exc)
            total = round(days * DEFAULT_TEMPLATE_COST_PER_DAY, 2)
            return CostResult(
                days=days,
                total=total,
                within_budget=None if intent.budget is None else total <= intent.budget,
            )

    # ------------------------------------------------------------ LLM 通路

    async def _generate_with_tools(self, intent: Intent) -> TripPlan | None:
        """Function Calling 多轮循环：模型决定调什么工具，结果回传给模型。

        每轮把模型返回的 `tool_calls` 逐个执行，并把工具结果以 `role=tool`
        消息追加进上下文，再进行下一轮；模型不再发起工具调用时，解析其最终
        JSON 并做字段补全。最多 `MAX_TOOL_ROUNDS` 轮，超出或无有效产出则返回
        `None`，由调用方降级为模板方案。

        Returns:
            解析成功且完成补全的 `TripPlan`；任何一步失败返回 `None`。
        """
        messages = self._build_messages(intent)
        for round_index in range(MAX_TOOL_ROUNDS):
            message = await self._call_deepseek(messages, tools=TOOL_DEFINITIONS)
            if message is None:
                return None

            tool_calls = message.get("tool_calls") or []
            messages.append(_assistant_message(message))
            if not tool_calls:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    return None
                plan = _parse_llm_plan(content, intent)
                if plan is None:
                    return None
                return await self._enrich_plan(plan, intent)

            LOGGER.info(
                "Function Calling 第 %d/%d 轮：模型请求 %d 个工具",
                round_index + 1,
                MAX_TOOL_ROUNDS,
                len(tool_calls),
            )
            for call in tool_calls:
                messages.append(await self._run_tool_call(call))

        LOGGER.warning("Function Calling 达到 %d 轮上限仍未产出行程", MAX_TOOL_ROUNDS)
        return None

    async def _run_tool_call(self, call: dict[str, Any]) -> dict[str, Any]:
        """执行单个 tool_call，返回可回传模型的 `role=tool` 消息。

        参数 JSON 解析失败或工具报错时**不中断整轮推理** —— 把错误文本作为
        工具结果回传，让模型自行修正（例如换城市、减少天数）。
        """
        call_id = str(call.get("id") or "")
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments 必须是 JSON 对象")
            result = await self._tools.call(name, **arguments)
            content = _serialize_tool_result(result)
        except Exception as exc:  # 参数错误 / 工具错误统一回传给模型
            LOGGER.warning("工具 %s 调用失败，错误已回传模型: %s", name, exc)
            content = json.dumps({"error": str(exc)}, ensure_ascii=False)
        return {"role": "tool", "tool_call_id": call_id, "content": content}

    async def _enrich_plan(self, plan: TripPlan, intent: Intent) -> TripPlan:
        """AI 行程的兜底补全：缺天气 / 缺花费时调工具补齐。

        `_parse_llm_plan` 已保证日程天数与用户需求一致，这里只补「模型没给」
        的增强字段，不改动模型给出的有效数据。补全失败时原样返回计划 ——
        补全属于增强项，不应把一份合法行程拖成降级方案。
        """
        data = plan.model_dump()
        changed = False

        if any(day.get("weather") is None for day in data["daily"]):
            weather = await self._fetch_weather_aligned(
                plan.destination, _intent_start_date(intent), plan.days
            )
            for offset, day in enumerate(data["daily"]):
                if day.get("weather") is None and offset < len(weather):
                    day["weather"] = weather[offset].model_dump(mode="json")
                    changed = True

        # 多日天气报告：优先保留模型给出的，缺失时从逐日天气汇总。
        if not data.get("weather_report"):
            report = [day["weather"] for day in data["daily"] if day.get("weather")]
            if report:
                data["weather_report"] = report
                changed = True

        cost_result: CostResult | None = None
        if data["total_cost"] <= 0 and data["daily"]:
            spots = [
                spot["name"] for day in data["daily"] for spot in day.get("spots", [])
            ]
            cost_result = await self._fetch_cost(intent, plan.days, spots)
            per_day = round(cost_result.total / plan.days, 2)
            for offset, day in enumerate(data["daily"]):
                if day["estimated_cost"] <= 0:
                    day["estimated_cost"] = (
                        round(cost_result.total - per_day * (plan.days - 1), 2)
                        if offset == plan.days - 1
                        else per_day
                    )
            data["total_cost"] = round(
                sum(day["estimated_cost"] for day in data["daily"]), 2
            )
            changed = True

        # 花费分项：模型未给出时按工具估算补齐。
        # 补齐后以 cost_breakdown 为准覆盖 total_cost / estimated_cost ——
        # LLM 经常瞎给旧默认值（600/1800），而 cost 工具走了正确路径（含知识库）。
        if data.get("cost_breakdown") is None:
            if cost_result is None:
                spots = [
                    spot["name"]
                    for day in data["daily"]
                    for spot in day.get("spots", [])
                ]
                cost_result = await self._fetch_cost(intent, plan.days, spots)
            data["cost_breakdown"] = cost_result.model_dump(mode="json")
            changed = True

        # 以 cost_breakdown 为权威数据源，强制覆盖 LLM 瞎给的 total_cost / estimated_cost。
        cb_data = data.get("cost_breakdown")
        if cb_data and cb_data.get("total", 0) > 0 and plan.days > 0:
            lodging = cb_data.get("lodging_total", 0)
            food = cb_data.get("food_total", 0)
            transit = cb_data.get("transport_total", 0)
            per_day_fixed = round((lodging + food + transit) / plan.days, 2)
            # 仅当 LLM 给的值与知识库不符时才覆盖（避免无意义的数据跳动）
            if round(data["total_cost"], 2) != round(cb_data["total"], 2):
                data["total_cost"] = round(cb_data["total"], 2)
                changed = True
            for day in data["daily"]:
                if round(day.get("estimated_cost", 0), 2) != per_day_fixed:
                    day["estimated_cost"] = per_day_fixed
                    changed = True

        if not data.get("source") or data["source"] == "unknown":
            data["source"] = "llm"
            changed = True

        if not changed:
            return plan
        try:
            return TripPlan.model_validate(data)
        except ValidationError:
            LOGGER.warning("AI 行程补全后校验失败，保留模型原始产出")
            return plan

    async def _call_deepseek(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """发起一次 DeepSeek 调用，返回 assistant message 对象。

        支持携带 `tools`（OpenAI 兼容的 Function Calling 描述）。内部捕获
        超时 / 网络错误 / HTTP 非 2xx / 响应体解析异常，**不向外抛出** ——
        由 `generate_itinerary` 统一决定是否降级。原始异常存入日志。

        Args:
            messages: 完整的对话消息列表（system / user / assistant / tool）。
            tools: 工具描述列表；为 None 时不携带工具。

        Returns:
            `choices[0].message`（dict，可能含 `content` 与 `tool_calls`）；
            `USE_MOCK_LLM` 已开启、未配置 API Key 或调用失败时为 None。
        """
        # Mock 开关先于 Key 判断：设了 Key 但要求 Mock 时同样不出网 ——
        # 这是「不想调用」的表达，不该被 Key 的存在推翻。
        if self._mock_llm:
            LOGGER.info("USE_MOCK_LLM 已开启，跳过真实 LLM 调用，直接走模板方案")
            return None
        if not self._api_key:
            return None
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await _llm_post(
                f"{self._base_url}/chat/completions",
                payload=payload,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
            message = body["choices"][0]["message"]
        except Exception as exc:
            LOGGER.warning("DeepSeek 调用失败: %s", exc)
            return None
        return message if isinstance(message, dict) else None

    def _build_messages(self, intent: Intent) -> list[dict[str, Any]]:
        """构造初始对话：系统提示词 + 用户需求。"""
        return [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_prompt(intent)},
        ]

    def _build_prompt(self, intent: Intent) -> str:
        """构造用户需求描述（最终 JSON 的输出要求已在系统提示词中声明）。"""
        days = intent.days or DEFAULT_TEMPLATE_DAYS
        start = _intent_start_date(intent)
        budget = "未提供" if intent.budget is None else f"{intent.budget:.0f} 元"
        preferences = "、".join(intent.preferences) or "无特别偏好"
        return (
            f"请规划行程。目的地 {intent.destination}，出发日期 {start.isoformat()}"
            f"（第 1 天），天数 {days}，预算 {budget}，偏好 {preferences}。"
            f"先调用工具获取真实数据，再输出最终 JSON；"
            f"spots 的数组顺序即当日游览路线顺序。"
        )


# ---------------------------------------------------------------- 模块级辅助


async def _llm_post(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> httpx.Response:
    """发起一次异步 HTTP POST。

    单独成函数，既把 `httpx.AsyncClient` 的生命周期收敛在一处，也给测试留下
    稳定的打桩点（无需 mock 上下文管理器）。

    Args:
        url: 目标地址。
        payload: JSON 请求体。
        headers: 请求头（含鉴权）。
        timeout: 超时秒数。

    Returns:
        httpx 响应对象。
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, json=payload, headers=headers)


def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """把模型返回的 assistant 消息整理为下一轮上下文中的一条历史消息。

    OpenAI 兼容接口要求历史里保留 `tool_calls`（含 id / function.arguments）
    以便与后续 `role=tool` 消息按 `tool_call_id` 对应；多余字段一律丢弃。
    """
    result: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content"),
    }
    tool_calls = message.get("tool_calls")
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result


def _serialize_tool_result(result: Any) -> str:
    """把工具返回值序列化为可回传模型的 JSON 文本。

    Pydantic 模型及其列表按 `model_dump(mode="json")` 展开；其余类型直接
    交给 `json.dumps`（内置工具均返回模型，此处是防御性分支）。
    """
    if isinstance(result, BaseModel):
        payload: Any = result.model_dump(mode="json")
    elif isinstance(result, list):
        payload = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in result
        ]
    else:
        payload = result
    return json.dumps(payload, ensure_ascii=False)


def _env_flag(name: str) -> bool:
    """把环境变量按布尔语义解析（见 docs/module-sdd/config.md §2.2）。

    取 `1` / `true` / `yes` / `on`（去空白、忽略大小写）为真，其余一律为假。
    **非法值不抛异常** —— 配置写错不该让应用起不来，归为「未启用」即可。

    Args:
        name: 环境变量名。

    Returns:
        该变量是否为真值；未设置、为空串或写法无法识别时返回 False。
    """
    return (os.getenv(name) or "").strip().lower() in _TRUTHY_VALUES


def _to_spots(items: list[Attraction]) -> list[Spot]:
    """Attraction → Spot，补上缺失的时长与票价。

    Mock 景点池不带 `suggested_duration_min` / `ticket_price`，缺失时取默认时长、
    票价记 0（免费）。单独成函数是为了把这段字段补全的细节从 `generate_template`
    的编排逻辑里摘出来。
    """
    return [
        Spot(
            name=item.name,
            duration_min=item.suggested_duration_min or DEFAULT_TEMPLATE_SPOT_DURATION_MIN,
            ticket_price=item.ticket_price or 0.0,
        )
        for item in items
    ]


def _find_earliest(text: str, candidates: tuple[str, ...]) -> str | None:
    """返回在 `text` 中出现位置最靠前的候选词。"""
    best_index: int | None = None
    best: str | None = None
    for candidate in candidates:
        index = text.find(candidate)
        if index >= 0 and (best_index is None or index < best_index):
            best_index, best = index, candidate
    return best


def _clean_destination(candidate: str) -> str | None:
    """清洗目的地候选词：剥离动词前缀、截断噪声字，长度不足 2 时判失败。"""
    cleaned = (candidate or "").strip()
    # 前两个字不参与噪声截断，避免「天津」「天山」被首字误伤。
    for index, char in enumerate(cleaned):
        if index >= 2 and char in _DESTINATION_NOISE:
            cleaned = cleaned[:index]
            break

    changed = True
    while changed and cleaned:
        changed = False
        for prefix in _DESTINATION_PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                changed = True
                break
    return cleaned if len(cleaned) >= 2 else None


def _match_date(text: str) -> tuple[re.Match[str] | None, date | None]:
    """从文本中解析出发日期，返回 (匹配片段, 日期)。

    优先级：ISO 日期 > 「X 月 Y 日」> 相对词（今天 / 明天 / 后天 / 大后天）。
    返回匹配片段是为了让调用方在预算提取前挖空数字，避免年份被当成预算。
    """
    match = _ISO_DATE_PATTERN.search(text)
    if match:
        parsed = _build_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed is not None:
            return match, parsed

    match = _MONTH_DAY_PATTERN.search(text)
    if match:
        parsed = _build_date(date.today().year, int(match.group(1)), int(match.group(2)))
        if parsed is not None:
            # 只报月日且该日期今年已过时，顺延到明年。
            if parsed < date.today():
                parsed = _build_date(parsed.year + 1, parsed.month, parsed.day) or parsed
            return match, parsed

    for word, offset in _RELATIVE_DATE_OFFSETS.items():
        if word in text:
            return None, date.today() + timedelta(days=offset)
    return None, None


def _build_date(year: int, month: int, day: int) -> date | None:
    """构造日期；非法月日（如 2 月 30 日）返回 None。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _default_start_date() -> date:
    """未指定出发日期时的默认值：明天。"""
    return date.today() + timedelta(days=DEFAULT_START_OFFSET_DAYS)


def _intent_start_date(intent: Intent) -> date:
    """取 Intent 的出发日期；未赋值时回落明天。"""
    return intent.start_date or _default_start_date()


def _mask_span(text: str, match: re.Match[str]) -> str:
    """把已匹配的片段替换为空白，避免同一串数字被二次识别。"""
    return f"{text[: match.start()]}{' ' * (match.end() - match.start())}{text[match.end() :]}"


def _safe_days(raw: str) -> int | None:
    """天数转 int；超出 1–30 时返回 None（不猜测用户意图）。"""
    value = int(raw)
    return value if 1 <= value <= _MAX_DAYS else None


def _extract_budget(text: str) -> float | None:
    """在文本中找 3–5 位数字作为预算，优先取邻近「预算 / 元 / 以内」等提示的。"""
    matches = list(_BUDGET_PATTERN.finditer(text))
    if not matches:
        return None
    for match in matches:
        start = max(0, match.start() - _BUDGET_HINT_WINDOW)
        end = min(len(text), match.end() + _BUDGET_HINT_WINDOW)
        if any(hint in text[start:end] for hint in _BUDGET_HINTS):
            return float(match.group(1))
    return float(matches[0].group(1))


def _extract_preferences(text: str) -> list[str]:
    """按关键词检测偏好标签，保持 `PREFERENCE_KEYWORDS` 的声明顺序。"""
    return [
        label
        for label, keywords in PREFERENCE_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]


def _score_confidence(
    days: int | None, budget: float | None, preferences: list[str]
) -> float:
    """按解析出的字段数给出置信度（目的地缺失时根本走不到这里）。"""
    score = 0.55
    if days is not None:
        score += 0.2
    if budget is not None:
        score += 0.15
    if preferences:
        score += 0.1
    return round(min(score, 1.0), 2)


def _distribute(items: list[Attraction], days: int) -> list[list[Attraction]]:
    """把景点轮流分配到各天 —— 轮转分配让每天都有热门景点，而非前紧后空。

    景点数少于天数时，为空日程补一个轮换景点，保证 AC-1 要求的「每日景点
    列表」不出现空白天（Mock 池最少 3 个景点，故一定有可补的项）。
    """
    buckets: list[list[Attraction]] = [[] for _ in range(days)]
    for index, item in enumerate(items):
        buckets[index % days].append(item)
    for offset, bucket in enumerate(buckets):
        if not bucket and items:
            bucket.append(items[offset % len(items)])
    return buckets


def _split_day_cost(total: float, days: int, offset: int) -> float:
    """把总额摊到每天；最后一天取余额，保证各天之和恰好等于总额。"""
    per_day = round(total / days, 2)
    if offset == days - 1:
        return round(total - per_day * (days - 1), 2)
    return per_day


def _strip_code_fence(raw: str) -> str:
    """剥掉可能包裹 JSON 的 Markdown 代码块围栏。"""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = text.split("\n", 1)[1] if "\n" in text else text
    return text.rsplit("```", 1)[0].strip()


def _parse_llm_plan(raw: str, intent: Intent) -> TripPlan | None:
    """把 LLM 输出解析为 TripPlan；任何不合法都返回 None 以触发降级。

    用户明确表达的 destination / days / budget 以 `intent` 为准 —— 模型不得
    改写用户需求。`daily` 多于天数时截断；**少于**天数时返回 None 交回模板
    方案 —— 缺的那几天没有内容可补，宁可整体降级，也不产出「days=5 但只有
    3 天日程」的残缺方案（前端按 `days` 渲染，多余天数会显示为空）。

    `total_cost` 一律按最终 `daily` 重算：沿用模型给的原值会在截断后产生
    「总额按 5 天算、明细只有 3 天」的矛盾。

    回填用 `model_validate` 而非 `model_copy` —— 后者不触发校验器，会让
    `TripPlan._daily_must_not_exceed_days` 形同虚设。
    """
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError):
        return None
    try:
        plan = TripPlan.model_validate(payload)
    except ValidationError:
        return None

    effective_days = intent.days if intent.days is not None else plan.days
    if len(plan.daily) < effective_days:
        LOGGER.warning(
            "LLM 只返回 %d 天日程，少于需求的 %d 天，降级为模板方案",
            len(plan.daily),
            effective_days,
        )
        return None

    daily = plan.daily[:effective_days]
    # 出发日期以 intent 为准：模型可能给错日期，统一按「出发日 + 偏移」重排，
    # 保证全链路（意图 → 日程 → 天气）使用同一时间轴。
    start = _intent_start_date(intent)
    daily = [
        day.model_copy(update={"date": start + timedelta(days=offset)})
        for offset, day in enumerate(daily)
    ]
    # 用 cost_breakdown（知识库/档位 cost 工具的真实返回）覆盖 LLM 瞎给的值。
    # LLM 生成的 estimated_cost/total_cost 经常是旧默认值 600/1800，
    # 而 cost_breakdown 走了正确路径（含知识库），以它为准。
    cb = plan.cost_breakdown
    if cb is not None and cb.total > 0 and effective_days > 0:
        per_day_fixed = round(
            (cb.lodging_total + cb.food_total + cb.transport_total) / effective_days, 2
        )
        # 每日 estimated_cost = 固定成本平摊（住宿+餐饮+交通）
        daily = [
            day.model_copy(update={"estimated_cost": per_day_fixed}) for day in daily
        ]
        total_cost = round(cb.total, 2)
    else:
        total_cost = round(sum(day.estimated_cost for day in daily), 2)

    updates: dict[str, object] = {
        "degraded": False,
        "destination": intent.destination,
        "days": effective_days,
        "daily": daily,
        "total_cost": total_cost,
        "source": "llm",
    }
    if intent.budget is not None:
        updates["budget"] = intent.budget
    try:
        return TripPlan.model_validate({**plan.model_dump(), **updates})
    except ValidationError:
        return None
