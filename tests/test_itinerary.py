"""TripPlan 跨字段校验单测 —— `daily` 与 `days` 的一致性。

对应 SDD docs/module-sdd/{reasoning,memory}.md：`days` 是前端渲染天数的依据，
`daily` 不得多于它。这条约束的由来是 `ReasoningEngine._parse_llm_plan` 曾用
`model_copy` 回填 `days` 而绕过了校验（见 docs/tasks/code_review_report.md F1）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import DailyPlan, TripPlan


def _day(index: int) -> DailyPlan:
    return DailyPlan(day_index=index)


def test_daily_may_be_empty() -> None:
    """只关心行程抬头时 daily 可以为空 —— 上界校验不要求两者相等。"""
    plan = TripPlan(destination="北京", days=5)

    assert plan.daily == []


def test_daily_length_may_equal_days() -> None:
    plan = TripPlan(destination="北京", days=2, daily=[_day(1), _day(2)])

    assert len(plan.daily) == plan.days


def test_daily_longer_than_days_is_rejected() -> None:
    with pytest.raises(ValidationError, match="超过 days"):
        TripPlan(destination="北京", days=1, daily=[_day(1), _day(2)])


def test_daily_longer_than_days_is_rejected_on_model_validate() -> None:
    """`model_validate` 路径同样拦截 —— 这正是 `model_copy` 漏掉的那一层。"""
    payload = {
        "destination": "北京",
        "days": 1,
        "daily": [{"day_index": 1}, {"day_index": 2}],
    }

    with pytest.raises(ValidationError):
        TripPlan.model_validate(payload)
