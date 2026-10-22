"""ReasoningEngine 单测。

对应 SDD docs/module-sdd/reasoning.md（含降级规则表、工具失败粒度表与
Function Calling 多轮循环）。

全部用例**不发起网络请求**：要么走模板降级路径，要么 monkeypatch 掉
DeepSeek 通路。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import pytest

from src.exceptions import (
    DestinationInvalidError,
    IntentUnrecognizedError,
    LLMUnavailableError,
    ToolCallError,
)
from src.mcp_client import ENV_AMAP_MCP_KEY, ENV_AMAP_MCP_URL
from src.models import Intent, TripPlan
from src.reasoning import (
    DEFAULT_TEMPLATE_COST_PER_DAY,
    DEFAULT_TEMPLATE_DAYS,
    DEFAULT_TEMPLATE_SPOTS,
    ENV_API_KEY,
    ENV_USE_MOCK_LLM,
    MAX_TOOL_ROUNDS,
    ReasoningEngine,
    _parse_llm_plan,
)
from src.tools import TOOL_DEFINITIONS, ToolCenter


class FailingToolCenter:
    """所有工具调用都失败的替身，用于验证「工具失败不阻断行程生成」。"""

    async def call(self, name: str, **params: object) -> object:
        raise ToolCallError(f"{name} 工具不可用")


@pytest.fixture
def engine() -> ReasoningEngine:
    return ReasoningEngine(ToolCenter(), api_key="")


@pytest.fixture
def broken_engine() -> ReasoningEngine:
    return ReasoningEngine(FailingToolCenter(), api_key="")  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉两个会让用例行为随开发机而变的变量。

    `main.py` 在导入期调用 `load_dotenv()`，而 `tests/test_routes.py` 会导入
    `main` —— 于是开发机 `.env` 里的值会**渗进整个测试进程**。若本机把
    `USE_MOCK_LLM` 设成 true，`_call_deepseek` 会在出网前直接返回 None，
    「成功取回内容」等用例就会莫名其妙地挂；`DEEPSEEK_API_KEY` 同理，
    留着它会让用例真的打向 DeepSeek。

    需要这两个变量的用例自行 `monkeypatch.setenv`（fixture 在用例之前生效，
    用例内的设置会覆盖它）。与 `tests/test_tools.py` 清 `MCP_WEATHER_URL` 同理。
    """
    monkeypatch.delenv(ENV_USE_MOCK_LLM, raising=False)
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    # 同理会渗进 `ToolCenter()`；清掉后所有用例都走 Mock 白名单校验。
    monkeypatch.delenv(ENV_AMAP_MCP_URL, raising=False)
    monkeypatch.delenv(ENV_AMAP_MCP_KEY, raising=False)


def _message(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造 OpenAI 兼容的 assistant message。"""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _stub_engine(engine: ReasoningEngine, *messages: dict[str, Any]) -> None:
    """把 `_call_deepseek` 换成按顺序返回给定消息的异步桩。"""
    pending = list(messages)

    async def fake(
        messages_in: list[dict[str, Any]],
        tools: object = None,
    ) -> dict[str, Any] | None:
        return pending.pop(0) if pending else None

    engine._api_key = "测试用密钥"
    engine._call_deepseek = fake  # type: ignore[method-assign]


# ---------------------------------------------------------------- parse_intent


def test_parse_intent_extracts_all_fields(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("帮我规划去北京玩3天，预算5000元，想吃美食")

    assert isinstance(intent, Intent)
    assert intent.destination == "北京"
    assert intent.days == 3
    assert intent.budget == 5000.0
    assert intent.preferences == ["美食"]
    assert intent.raw_text == "帮我规划去北京玩3天，预算5000元，想吃美食"


def test_parse_intent_keeps_days_and_budget_apart(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("去上海5天，预算3000")

    assert intent.days == 5
    assert intent.budget == 3000.0


def test_parse_intent_extracts_unknown_city_from_verb_phrase(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("去西安玩4天")

    assert intent.destination == "西安"
    assert intent.days == 4


def test_parse_intent_ignores_out_of_range_days(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("去北京100天")

    assert intent.destination == "北京"
    assert intent.days is None


def test_parse_intent_leaves_budget_none_when_absent(engine: ReasoningEngine) -> None:
    assert engine.parse_intent("去成都玩两天").budget is None


def test_parse_intent_detects_multiple_preferences(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("去北京，喜欢美食、购物和文化")

    assert intent.preferences == ["美食", "购物", "文化"]


def test_parse_intent_confidence_within_range(engine: ReasoningEngine) -> None:
    assert 0.0 < engine.parse_intent("去北京玩3天，预算5000").confidence <= 1.0


@pytest.mark.parametrize("text", ["", "   ", "今天天气怎么样"])
def test_parse_intent_without_destination_raises(engine: ReasoningEngine, text: str) -> None:
    with pytest.raises(IntentUnrecognizedError):
        engine.parse_intent(text)


# ---------------------------------------------------------------- 出发日期解析


def test_parse_intent_defaults_start_date_to_tomorrow(engine: ReasoningEngine) -> None:
    assert engine.parse_intent("去北京玩3天").start_date == date.today() + timedelta(days=1)


@pytest.mark.parametrize(
    ("text", "offset"),
    [
        ("今天去北京", 0),
        ("明天去北京玩3天", 1),
        ("后天去北京玩3天", 2),
        ("大后天去北京玩3天", 3),
    ],
)
def test_parse_intent_parses_relative_dates(
    engine: ReasoningEngine, text: str, offset: int
) -> None:
    assert engine.parse_intent(text).start_date == date.today() + timedelta(days=offset)


def test_parse_intent_parses_iso_date_with_chinese_separators(
    engine: ReasoningEngine,
) -> None:
    intent = engine.parse_intent("2026年10月25日去北京玩3天，预算5000元")

    assert intent.start_date == date(2026, 10, 25)
    # 年份 / 月日不得被预算提取误识别
    assert intent.budget == 5000.0


def test_parse_intent_parses_iso_date_with_dashes(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("去北京玩3天，出发日期 2026-10-25，预算3000")

    assert intent.start_date == date(2026, 10, 25)
    assert intent.budget == 3000.0


def test_parse_intent_parses_month_day_and_rolls_to_next_year(
    engine: ReasoningEngine,
) -> None:
    today = date.today()
    expected = date(today.year, 10, 25)
    if expected < today:
        expected = date(today.year + 1, 10, 25)

    intent = engine.parse_intent("10月25日去北京玩3天，预算5000元")

    assert intent.start_date == expected
    assert intent.budget == 5000.0


def test_parse_intent_passed_start_date_wins_over_text(engine: ReasoningEngine) -> None:
    intent = engine.parse_intent("去北京玩3天，10月25日出发", date(2027, 1, 1))

    assert intent.start_date == date(2027, 1, 1)


# ---------------------------------------------------------------- 未收录城市提取


@pytest.mark.parametrize(
    ("text", "destination", "days"),
    [
        ("西安4日游", "西安", 4),
        ("天津3天", "天津", 3),
        ("帮我规划西安4日游", "西安", 4),
        ("三亚三天", "三亚", None),
    ],
)
def test_parse_intent_extracts_city_outside_preset_list(
    engine: ReasoningEngine, text: str, destination: str, days: int | None
) -> None:
    intent = engine.parse_intent(text)

    assert intent.destination == destination
    assert intent.days == days


async def test_generate_itinerary_rejects_invalid_destination(
    engine: ReasoningEngine,
) -> None:
    intent = engine.parse_intent("去西安玩3天")

    with pytest.raises(DestinationInvalidError):
        await engine.generate_itinerary(intent)


async def test_generate_template_aligns_weather_with_start_date(
    engine: ReasoningEngine,
) -> None:
    start = date.today() + timedelta(days=5)
    intent = engine.parse_intent("去北京玩3天", start)

    plan = await engine.generate_template(intent)

    expected = [start + timedelta(days=offset) for offset in range(3)]
    assert [day.date for day in plan.daily] == expected
    assert [item.date for item in plan.weather_report] == expected


async def test_generate_template_fills_enhanced_fields(engine: ReasoningEngine) -> None:
    plan = await engine.generate_template(engine.parse_intent("去北京玩2天"))

    assert plan.source == "template"
    assert len(plan.weather_report) == 2
    assert plan.cost_breakdown is not None
    assert plan.cost_breakdown.total == plan.total_cost


# ---------------------------------------------------------------- 模板生成


async def test_generate_template_is_degraded(engine: ReasoningEngine) -> None:
    plan = await engine.generate_template(
        Intent(raw_text="去北京玩3天", destination="北京", days=3)
    )

    assert isinstance(plan, TripPlan)
    assert plan.degraded is True


async def test_generate_template_builds_one_entry_per_day(engine: ReasoningEngine) -> None:
    plan = await engine.generate_template(
        Intent(raw_text="去北京玩3天", destination="北京", days=3)
    )

    assert plan.days == 3
    assert [day.day_index for day in plan.daily] == [1, 2, 3]
    assert all(day.spots for day in plan.daily)


async def test_generate_template_includes_weather_and_route_order(
    engine: ReasoningEngine,
) -> None:
    plan = await engine.generate_template(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert all(day.weather is not None for day in plan.daily)
    # route 工具保持输入顺序，故每日景点顺序即分配顺序，且不得重排
    assert plan.daily[0].spots[0].name == "故宫"


async def test_generate_template_daily_costs_sum_to_total(engine: ReasoningEngine) -> None:
    plan = await engine.generate_template(
        Intent(raw_text="去北京玩3天", destination="北京", days=3)
    )

    assert pytest.approx(plan.total_cost) == sum(day.estimated_cost for day in plan.daily)


async def test_generate_template_defaults_to_three_days(engine: ReasoningEngine) -> None:
    plan = await engine.generate_template(Intent(raw_text="去北京", destination="北京"))

    assert plan.days == DEFAULT_TEMPLATE_DAYS
    assert len(plan.daily) == DEFAULT_TEMPLATE_DAYS


async def test_generate_template_without_destination_raises(engine: ReasoningEngine) -> None:
    with pytest.raises(IntentUnrecognizedError):
        await engine.generate_template(Intent(raw_text="随便逛逛"))


async def test_generate_template_falls_back_when_tools_fail(
    broken_engine: ReasoningEngine,
) -> None:
    plan = await broken_engine.generate_template(
        Intent(raw_text="去北京玩2天", destination="北京", days=2, budget=1000)
    )

    assert plan.degraded is True
    assert plan.total_cost == 2 * DEFAULT_TEMPLATE_COST_PER_DAY
    assert plan.daily[0].spots[0].name == "故宫"
    assert all(day.weather is None for day in plan.daily)


async def test_generate_template_uses_default_pool_for_unknown_city(
    broken_engine: ReasoningEngine,
) -> None:
    plan = await broken_engine.generate_template(
        Intent(raw_text="去某地玩1天", destination="某地", days=1)
    )

    assert [spot.name for spot in plan.daily[0].spots] == DEFAULT_TEMPLATE_SPOTS


# ---------------------------------------------------------------- generate_itinerary


async def test_generate_itinerary_without_api_key_degrades(engine: ReasoningEngine) -> None:
    plan = await engine.generate_itinerary(
        Intent(raw_text="去杭州玩2天", destination="杭州", days=2)
    )

    assert plan.degraded is True
    assert plan.destination == "杭州"


async def test_generate_itinerary_without_destination_raises(engine: ReasoningEngine) -> None:
    with pytest.raises(IntentUnrecognizedError):
        await engine.generate_itinerary(Intent(raw_text="随便逛逛"))


async def test_generate_itinerary_uses_llm_when_available(engine: ReasoningEngine) -> None:
    payload = {
        "destination": "北京",
        "days": 2,
        "total_cost": 1500,
        "daily": [
            {"day_index": 1, "spots": [{"name": "故宫"}], "estimated_cost": 500},
            {"day_index": 2, "spots": [{"name": "长城"}], "estimated_cost": 500},
        ],
    }
    _stub_engine(engine, _message(json.dumps(payload)))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is False
    assert [spot.name for spot in plan.daily[0].spots] == ["故宫"]


async def test_generate_itinerary_llm_days_follow_intent(engine: ReasoningEngine) -> None:
    payload = {
        "destination": "上海",
        "days": 5,
        "total_cost": 1,
        "daily": [
            {"day_index": index + 1, "spots": [], "estimated_cost": 1}
            for index in range(5)
        ],
    }
    _stub_engine(engine, _message(f"```json\n{json.dumps(payload)}\n```"))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去上海玩3天", destination="上海", days=3)
    )

    assert plan.days == 3
    assert len(plan.daily) == 3


@pytest.mark.parametrize("raw", ["不是 JSON", "{}", '{"destination": "北京", "days": 0}'])
async def test_generate_itinerary_falls_back_on_bad_llm_output(
    engine: ReasoningEngine, raw: str
) -> None:
    _stub_engine(engine, _message(raw))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is True


async def test_generate_itinerary_raises_when_template_also_fails(
    engine: ReasoningEngine,
) -> None:
    async def boom(intent: Intent) -> TripPlan:
        raise RuntimeError("模板也挂了")

    engine.generate_template = boom  # type: ignore[method-assign]

    with pytest.raises(LLMUnavailableError):
        await engine.generate_itinerary(
            Intent(raw_text="去北京玩2天", destination="北京", days=2)
        )


# ---------------------------------------------------------------- Function Calling


TOOL_CALL_ATTRACTIONS = {
    "id": "call_1",
    "type": "function",
    "function": {
        "name": "attractions",
        "arguments": json.dumps({"city": "北京", "limit": 2}, ensure_ascii=False),
    },
}


def _final_payload(days: int = 2, *, weather: bool = True) -> dict[str, Any]:
    daily: list[dict[str, Any]] = []
    for index in range(days):
        day: dict[str, Any] = {
            "day_index": index + 1,
            "date": f"2026-10-{2 + index:02d}",
            "spots": [{"name": "故宫", "duration_min": 120, "ticket_price": 60}],
            "estimated_cost": 500,
        }
        if weather:
            day["weather"] = {
                "date": f"2026-10-{2 + index:02d}",
                "condition": "晴",
                "temp_high": 28,
                "temp_low": 22,
            }
        daily.append(day)
    return {
        "destination": "北京",
        "days": days,
        "total_cost": 500 * days,
        "daily": daily,
    }


async def test_generate_itinerary_runs_tool_calls_before_final_json(
    engine: ReasoningEngine,
) -> None:
    """模型先请求工具 → 工具结果回传 → 下一轮输出最终 JSON。"""
    seen_messages: list[list[dict[str, Any]]] = []

    async def fake(
        messages: list[dict[str, Any]], tools: object = None
    ) -> dict[str, Any]:
        seen_messages.append(list(messages))
        if len(seen_messages) == 1:
            return _message(None, [TOOL_CALL_ATTRACTIONS])
        return _message(json.dumps(_final_payload()))

    engine._api_key = "测试用密钥"
    engine._call_deepseek = fake  # type: ignore[method-assign]

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is False
    # 第二轮上下文里必须包含 assistant 的 tool_calls 与 role=tool 的结果
    second_round = seen_messages[1]
    assert any(
        message["role"] == "assistant" and message.get("tool_calls")
        for message in second_round
    )
    tool_messages = [message for message in second_round if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert "故宫" in tool_messages[0]["content"]


async def test_generate_itinerary_falls_back_after_max_tool_rounds(
    engine: ReasoningEngine,
) -> None:
    """轮数耗尽仍不产出最终 JSON 时降级为模板方案。"""

    async def fake(
        messages: list[dict[str, Any]], tools: object = None
    ) -> dict[str, Any]:
        return _message(None, [TOOL_CALL_ATTRACTIONS])

    engine._api_key = "测试用密钥"
    engine._call_deepseek = fake  # type: ignore[method-assign]

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is True
    assert MAX_TOOL_ROUNDS == 3


async def test_tool_call_error_is_reported_back_to_model(
    engine: ReasoningEngine,
) -> None:
    """工具报错回传给模型而非中断循环；模型可据此输出兜底 JSON。"""
    bad_call = {
        "id": "call_bad",
        "type": "function",
        "function": {"name": "route", "arguments": '{"city": "北京", "spots": []}'},
    }

    async def fake(
        messages: list[dict[str, Any]], tools: object = None
    ) -> dict[str, Any]:
        if not any(message["role"] == "tool" for message in messages):
            return _message(None, [bad_call])
        return _message(json.dumps(_final_payload()))

    engine._api_key = "测试用密钥"
    engine._call_deepseek = fake  # type: ignore[method-assign]

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is False


async def test_ai_plan_is_enriched_with_weather_and_cost(engine: ReasoningEngine) -> None:
    """AI 行程缺天气 / 花费时，自动调工具补全（增强字段）。"""
    payload = _final_payload(days=2, weather=False)
    for day in payload["daily"]:
        day["estimated_cost"] = 0
    payload["total_cost"] = 0
    _stub_engine(engine, _message(json.dumps(payload)))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is False
    assert all(day.weather is not None for day in plan.daily)
    assert plan.total_cost > 0
    assert pytest.approx(plan.total_cost) == sum(day.estimated_cost for day in plan.daily)


# ---------------------------------------------------------------- _call_deepseek


async def test_call_deepseek_returns_none_without_api_key(engine: ReasoningEngine) -> None:
    engine._api_key = None

    assert await engine._call_deepseek([{"role": "user", "content": "提示词"}]) is None


async def test_call_deepseek_returns_none_on_http_error(
    engine: ReasoningEngine, monkeypatch
) -> None:
    import src.reasoning as reasoning_module

    async def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("网络不通")

    engine._api_key = "测试用密钥"
    monkeypatch.setattr(reasoning_module, "_llm_post", boom)

    assert await engine._call_deepseek([{"role": "user", "content": "提示词"}]) is None


class _FakeResponse:
    """httpx 响应的最小替身。"""

    def __init__(self, content: object) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"role": "assistant", "content": self._content}}]}


async def test_call_deepseek_returns_message_on_success(
    engine: ReasoningEngine, monkeypatch
) -> None:
    import src.reasoning as reasoning_module

    async def fake(*args: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse('{"a": 1}')

    engine._api_key = "测试用密钥"
    monkeypatch.setattr(reasoning_module, "_llm_post", fake)

    message = await engine._call_deepseek([{"role": "user", "content": "提示词"}])

    assert message == {"role": "assistant", "content": '{"a": 1}'}


async def test_call_deepseek_sends_tools_and_waits_30s(
    engine: ReasoningEngine, monkeypatch
) -> None:
    import src.reasoning as reasoning_module

    captured: dict[str, Any] = {}

    async def spy(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse("{}")

    engine._api_key = "测试用密钥"
    monkeypatch.setattr(reasoning_module, "_llm_post", spy)

    await engine._call_deepseek(
        [{"role": "user", "content": "提示词"}], tools=TOOL_DEFINITIONS
    )

    payload = captured["payload"]
    assert payload["tools"] == TOOL_DEFINITIONS
    assert payload["tool_choice"] == "auto"
    assert captured["timeout"] == 30.0


@pytest.mark.parametrize("content", ["", "   ", None])
async def test_blank_final_content_falls_back_to_template(
    engine: ReasoningEngine, monkeypatch, content: object
) -> None:
    """模型最终消息没有内容也没有工具调用时，整体降级。"""
    import src.reasoning as reasoning_module

    async def fake(*args: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(content)

    engine._api_key = "测试用密钥"
    monkeypatch.setattr(reasoning_module, "_llm_post", fake)

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert plan.degraded is True


# ---------------------------------------------------------------- USE_MOCK_LLM


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", " true "])
def test_use_mock_llm_accepts_documented_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """真值表见 docs/module-sdd/config.md §2.2。"""
    monkeypatch.setenv(ENV_USE_MOCK_LLM, value)

    assert ReasoningEngine(ToolCenter(), api_key="测试用密钥")._mock_llm is True


@pytest.mark.parametrize("value", ["", "   ", "false", "0", "no", "off", "maybe", "2"])
def test_use_mock_llm_treats_everything_else_as_false(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """未列出的值一律为假，且**不抛异常** —— 配置写错不该让服务起不来。"""
    monkeypatch.setenv(ENV_USE_MOCK_LLM, value)

    assert ReasoningEngine(ToolCenter(), api_key="测试用密钥")._mock_llm is False


def test_use_mock_llm_defaults_to_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_USE_MOCK_LLM, raising=False)

    assert ReasoningEngine(ToolCenter(), api_key="测试用密钥")._mock_llm is False


async def test_use_mock_llm_skips_network_even_with_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配了 Key 但要求 Mock 时同样不出网 —— Key 的存在不该推翻「不想调用」。

    用**记账 spy** 而非抛异常的桩：`_call_deepseek` 的 `except Exception` 会把
    异常吞成 None，抛异常的桩无法区分「开关生效」与「开关失效但调用失败」——
    两种情形都得到 None，用例就成了空话。spy 返回一份**合法**响应，于是
    「被调用过」必然导致 `_call_deepseek` 返回内容，断言两侧都能证伪。
    """
    import src.reasoning as reasoning_module

    calls: list[str] = []

    async def spy(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse('{"destination": "北京", "days": 1, "daily": []}')

    monkeypatch.setenv(ENV_USE_MOCK_LLM, "true")
    monkeypatch.setattr(reasoning_module, "_llm_post", spy)
    engine = ReasoningEngine(ToolCenter(), api_key="测试用密钥")

    assert await engine._call_deepseek([{"role": "user", "content": "提示词"}]) is None
    assert calls == [], "USE_MOCK_LLM 开启时不应发起任何出网请求"


async def test_use_mock_llm_still_degrades_the_whole_itinerary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关落到端到端路径上：Mock 开启 → 直接给降级模板，而不是报错。

    桩返回一份**能通过校验**的行程 JSON：若开关失效，模型输出会被直接采用，
    `degraded` 变成 False，用例即失败 —— 不能只断言「结果是模板」，
    因为降级路径本身也可能因别的原因走到，那样就测不出开关。
    """
    import src.reasoning as reasoning_module

    calls: list[str] = []

    async def spy(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(json.dumps(_final_payload()))

    monkeypatch.setenv(ENV_USE_MOCK_LLM, "true")
    monkeypatch.setattr(reasoning_module, "_llm_post", spy)
    engine = ReasoningEngine(ToolCenter(), api_key="测试用密钥")

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天", destination="北京", days=2)
    )

    assert calls == [], "USE_MOCK_LLM 开启时不应发起任何出网请求"
    assert plan.degraded is True
    assert len(plan.daily) == 2


async def test_use_mock_llm_off_keeps_calling_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向用例：关掉开关后 Key 重新生效，通路必须回到网络调用。"""
    import src.reasoning as reasoning_module

    async def fake(*args: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse("内容")

    monkeypatch.setenv(ENV_USE_MOCK_LLM, "false")
    monkeypatch.setattr(reasoning_module, "_llm_post", fake)
    engine = ReasoningEngine(ToolCenter(), api_key="测试用密钥")

    message = await engine._call_deepseek([{"role": "user", "content": "提示词"}])

    assert message == {"role": "assistant", "content": "内容"}


async def test_use_mock_llm_logs_info_but_not_a_failure_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """配了 Key 又开 Mock 时，日志必须说的是「开关生效」而不是「DeepSeek 不可用」。

    降级本身不变，变的是**口吻**：这是用户主动选的离线模式，报成故障会把排查
    引向不存在的网络问题。故同时断言 INFO 那条在、WARNING 一条都没有。
    """
    monkeypatch.setenv(ENV_USE_MOCK_LLM, "true")
    engine = ReasoningEngine(ToolCenter(), api_key="测试用密钥")

    with caplog.at_level(logging.INFO, logger="src.reasoning"):
        plan = await engine.generate_itinerary(
            Intent(raw_text="去北京玩2天", destination="北京", days=2)
        )

    assert plan.degraded is True
    assert any("USE_MOCK_LLM" in record.message for record in caplog.records)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# ---------------------------------------------------------------- 其它分支


def test_parse_intent_budget_falls_back_to_first_number(engine: ReasoningEngine) -> None:
    # 数字附近没有「预算 / 元」等提示词时，取首个 3 位以上的数字
    intent = engine.parse_intent("去北京3天 8888")

    assert intent.budget == 8888.0


async def test_generate_template_fills_days_beyond_spot_count(engine: ReasoningEngine) -> None:
    # 深圳只有 4 个景点，排 5 天时最后一天须补景点，不能出现空白天
    plan = await engine.generate_template(
        Intent(raw_text="去深圳玩5天", destination="深圳", days=5)
    )

    assert len(plan.daily) == 5
    assert all(day.spots for day in plan.daily)


async def test_generate_itinerary_reraises_agent_error_from_template(
    engine: ReasoningEngine,
) -> None:
    async def boom(intent: Intent) -> TripPlan:
        raise IntentUnrecognizedError("模板内部的目的地校验失败")

    engine.generate_template = boom  # type: ignore[method-assign]

    # AgentError 必须原样冒泡，不得被包装成 LLMUnavailableError 而丢失错误码
    with pytest.raises(IntentUnrecognizedError):
        await engine.generate_itinerary(
            Intent(raw_text="去北京玩2天", destination="北京", days=2)
        )


async def test_generate_itinerary_llm_takes_intent_budget(engine: ReasoningEngine) -> None:
    payload = _final_payload(days=2)
    _stub_engine(engine, _message(json.dumps(payload)))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩2天，预算3000", destination="北京", days=2, budget=3000.0)
    )

    assert plan.budget == 3000.0
    assert plan.degraded is False


# ---------------------------------------------------------------- 天数与总额一致性（回归）


async def test_generate_itinerary_falls_back_when_llm_returns_fewer_days(
    engine: ReasoningEngine,
) -> None:
    """用户要 5 天、模型只给 3 天时必须整体降级。

    回归：此前 `model_copy` 会把 days 改成 5 而 daily 仍是 3 条，产出并入库
    「days=5 但只有 3 天日程」的残缺方案（缺的天数只能显示为空）。
    """
    payload = {
        "destination": "北京",
        "days": 3,
        "total_cost": 3000,
        "daily": [
            {"day_index": index + 1, "spots": [], "estimated_cost": 1000}
            for index in range(3)
        ],
    }
    _stub_engine(engine, _message(json.dumps(payload)))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去北京玩5天", destination="北京", days=5)
    )

    assert plan.degraded is True
    assert plan.days == 5
    assert len(plan.daily) == 5


async def test_generate_itinerary_llm_total_cost_follows_truncated_daily(
    engine: ReasoningEngine,
) -> None:
    """截断到 3 天后 total_cost 必须按留下的 3 天重算（5000 → 3000）。

    回归：此前沿用模型给的原值，会出现「总额对应 5 天、明细只有 3 天」的矛盾。
    """
    payload = {
        "destination": "上海",
        "days": 5,
        "total_cost": 5000,
        "daily": [
            {"day_index": index + 1, "spots": [], "estimated_cost": 1000}
            for index in range(5)
        ],
    }
    _stub_engine(engine, _message(json.dumps(payload)))

    plan = await engine.generate_itinerary(
        Intent(raw_text="去上海玩3天", destination="上海", days=3)
    )

    assert plan.degraded is False
    assert len(plan.daily) == 3
    assert plan.total_cost == 3000.0


def test_parse_llm_plan_returns_none_when_rebuild_fails() -> None:
    """回填后重建失败（目的地为空串，违反 min_length=1）返回 None，不向上抛。"""
    payload = {
        "destination": "北京",
        "days": 2,
        "daily": [{"day_index": index + 1, "spots": []} for index in range(2)],
    }

    plan = _parse_llm_plan(
        json.dumps(payload), Intent(raw_text="x", destination="", days=2)
    )

    assert plan is None
