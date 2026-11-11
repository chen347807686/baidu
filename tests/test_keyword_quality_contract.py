"""J01-J22 契约的可执行纯函数样例，不连接数据库。"""


import pytest

from dashboard.keyword_negative_rules import NegativeRuleProfile, judge_negative
from dashboard.keyword_quality import QualityInput, judge_quality

BASE = {"data_state": "complete", "rule_passed": True}


@pytest.mark.parametrize(
    ("case", "item", "expected"),
    [
        ("J01", QualityInput(20, 2, 200, **BASE), "high"),
        ("J02", QualityInput(19, 2, 100, **BASE), "pending"),
        ("J03", QualityInput(20, 1, 50, **BASE), "ordinary"),
        ("J04", QualityInput(20, 0, 200, **BASE), "ordinary"),
        ("J05", QualityInput(20, 2, "200.0001", **BASE), "ordinary"),
        ("J06", QualityInput(20, 2, 200, data_state="partial", rule_passed=True), "pending"),
        ("J07", QualityInput(20, 2, 200, **BASE), "high"),
        ("J08", QualityInput(20, 2, 200, rule_passed=None, data_state="complete"), "pending"),
        ("J09", QualityInput(20, 2, 200, manual_quality="high", **{**BASE, "rule_passed": True}), "high"),
        ("J10", QualityInput(20, 1, 50, manual_quality=None, **BASE), "ordinary"),
        ("J11", QualityInput(20, 2, 200, negative_confirmed=True, **BASE), "pending"),
        ("J20", QualityInput(20, 2, 200, **BASE), "high"),
        ("J21", QualityInput(0, 2, 200, **BASE), "pending"),
        ("J22", QualityInput(20, 2, 200, negative_confirmed=True, **BASE), "pending"),
    ],
)
def test_quality_contract_samples(case: str, item: QualityInput, expected: str) -> None:
    """固定样例使用测试阈值，不把阈值写入产品配置。"""
    result = judge_quality(item, min_clicks=20, min_leads=2, target_lead_cost=100)
    assert result.value == expected, case


def test_manual_override_survives_automatic_rerun() -> None:
    item = QualityInput(20, 1, 50, manual_quality="high", data_state="complete", rule_passed=True)
    result = judge_quality(item, min_clicks=20, min_leads=2, target_lead_cost=100)
    assert result.value == "high" and result.automatic == "ordinary"


def test_rule_only_marks_negative_without_relevance_classes() -> None:
    profile = NegativeRuleProfile.from_mapping({"招聘": ["招聘"]})
    result = judge_negative("黄金招聘", profile)
    assert result.matched and result.category == "招聘"


def test_negative_rule_keeps_business_intent_for_review() -> None:
    profile = NegativeRuleProfile.from_mapping({}, competitor_brands=["竞品"], intent_words=["回收"])
    result = judge_negative("竞品回收", profile)
    assert not result.matched and "待复核" in result.reason
