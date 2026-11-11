"""否词规则纯函数测试，不连接数据库或外部服务。"""

from dashboard.keyword_negative_rules import NegativeRuleProfile, judge_negative


def profile() -> NegativeRuleProfile:
    return NegativeRuleProfile.from_mapping(
        {"招聘": ["招聘", "兼职"], "维修": ["维修"]},
        competitor_brands=["竞品甲"],
        non_target_regions=["外地"],
        intent_words=["回收", "报价"],
    )


def test_custom_negative_has_priority() -> None:
    result = judge_negative("竞品甲招聘", profile(), ["竞品甲"])
    assert result.matched and result.category == "custom"


def test_category_and_reason_are_returned() -> None:
    result = judge_negative("黄金维修", profile())
    assert (result.matched, result.category) == (True, "维修")
    assert "维修" in result.reason


def test_brand_with_intent_is_left_for_review() -> None:
    result = judge_negative("竞品甲黄金回收", profile())
    assert not result.matched
    assert "待复核" in result.reason


def test_brand_without_intent_is_negative() -> None:
    result = judge_negative("竞品甲官网", profile())
    assert result.matched and result.category == "competitor_brand"


def test_empty_or_unknown_is_not_negative() -> None:
    assert not judge_negative("", profile()).matched
    assert not judge_negative("黄金回收", profile()).matched
