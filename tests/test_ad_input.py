"""投放输入与指纹的纯合成回归，无数据库、凭据或网络。

验证粒度稳定性、类型区别、金额边界、安全错误及不可变准备结果，避免重复
核验/改名制造新事实。此测试不代替真实来源完整维度与汇总规则的核实。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dashboard.ad_input import AdInputError, PreparedAd, prepare_ad_rows
from dashboard.schema import ad_daily


def sample(**changes: object) -> dict[str, object]:
    """提供字段齐全的规范输入，未知实体和指标必须显式为空。"""
    values: dict[str, object] = {
        "account_id": 1,
        "source_account_id": 1,
        "stat_date": "2026-11-10",
        "report_kind": "keyword",
        "project_id": None,
        "campaign_id": 1,
        "adgroup_id": 2,
        "keyword_id": 3,
        "creative_id": None,
        "keyword_text": "合成词",
        "search_term": None,
        "entity_name": "计划名",
        "device": "pc",
        "device_scope": "detail",
        "extra_dimensions": {},
        "impressions": None,
        "clicks": 0,
        "cost": "12.3400",
        "source_revision": "r1",
        "source_checked_at": datetime(2026, 11, 10, tzinfo=UTC),
        "ingested_at": datetime(2026, 11, 10, tzinfo=UTC),
    }
    return values | changes


def prepared(**changes: object) -> PreparedAd:
    """取单条准备结果，避免把批内重复限制与跨批更正测试混淆。"""
    return prepare_ad_rows([sample(**changes)])[0]


def test_stable_identity_and_content() -> None:
    """名称/指标更正不改主键，核验时间及修订号不改变内容指纹。"""
    first = prepared()
    renamed = prepared(keyword_text="改名", entity_name="新名称", cost="15")
    assert (
        first.grain_key == renamed.grain_key
        and first.content_hash != renamed.content_hash
    )
    repeated = prepared(
        source_revision="r2", ingested_at=datetime(2026, 11, 11, tzinfo=UTC)
    )
    assert first.content_hash == repeated.content_hash
    assert (
        prepared(extra_dimensions={"a": 1, "b": None}).grain_key
        == prepared(extra_dimensions={"b": None, "a": 1}).grain_key
    )
    assert (
        len(
            {
                prepared(extra_dimensions=extra).grain_key
                for extra in [
                    {},
                    {"v": None},
                    {"v": ""},
                    {"v": 1},
                    {"v": True},
                    {"v": "1"},
                ]
            }
        )
        == 6
    )
    assert prepared(device="mobile").grain_key != first.grain_key
    assert prepared(device=None, device_scope="summary").grain_key != first.grain_key
    assert (
        prepared(report_kind="search_term", search_term="词A").grain_key
        != prepared(report_kind="search_term", search_term="词a").grain_key
    )
    assert prepared(report_kind="creative", creative_id=4).grain_key != first.grain_key
    parameters = first.parameters()
    assert (
        parameters["cost"] == Decimal("12.3400") and parameters["impressions"] is None
    )
    assert set(parameters) == set(ad_daily.c.keys())
    parameters["dimensions"] = {}
    assert first.parameters()["dimensions"] != {}


@pytest.mark.parametrize(
    "changes",
    [
        {"cost": 1.2},
        {"cost": True},
        {"cost": "1.00001"},
        {"cost": "NaN"},
        {"cost": "100000000000000"},
        {"clicks": True},
        {"clicks": -1},
        {"clicks": "1"},
        {"keyword_id": None},
        {"source_checked_at": datetime(2026, 11, 10)},
        {"stat_date": "20261110"},
        {"stat_date": datetime(2026, 11, 10)},
        {"device_scope": "summary"},
        {"extra_dimensions": {"v": 1.5}},
        {"extra_dimensions": {"v": {"nested": 1}}},
        {"PRIVATE_FIELD": "secret"},
    ],
)
def test_invalid_input_is_redacted(changes: dict[str, object]) -> None:
    """非法值和未知列名不得通过异常原文泄漏，失败批次不返回部分候选。"""
    with pytest.raises(AdInputError) as caught:
        prepare_ad_rows([sample(), sample(**changes)])
    assert caught.value.row == 2
    assert "PRIVATE" not in str(caught.value) and "secret" not in str(caught.value)


def test_batch_boundaries() -> None:
    """重复主键拒绝，金额尾零允许，空批不做 I/O，输入数量显式有界。"""
    with pytest.raises(AdInputError, match="duplicate_grain"):
        prepare_ad_rows([sample(), sample(keyword_text="改名")])
    with pytest.raises(AdInputError, match="batch_too_large"):
        prepare_ad_rows([sample()] * 1001)
    assert prepare_ad_rows([]) == []
    assert prepared(cost="1.230000").parameters()["cost"] == Decimal("1.2300")
    assert prepared(cost=None).content_hash != prepared(cost="0").content_hash


def test_missing_fields_and_boundaries() -> None:
    """漏指标与显式未知分开；最大金额精确保留，超范围整数拒绝。"""
    incomplete = sample()
    incomplete.pop("clicks")
    with pytest.raises(AdInputError) as caught:
        prepare_ad_rows([incomplete])
    assert caught.value.fields == ("clicks",)
    assert prepared(cost="99999999999999.9999").parameters()["cost"] == Decimal(
        "99999999999999.9999"
    )
    with pytest.raises(AdInputError):
        prepared(impressions=18446744073709551616)
    with pytest.raises(AdInputError):
        prepared(keyword_text="词" * 22000)
    # 修改原始扩展字典不应污染已经准备好的不可变结果。
    original = sample(extra_dimensions={"match": "exact"})
    first = prepare_ad_rows([original])[0]
    extras = original["extra_dimensions"]
    assert isinstance(extras, dict)
    extras["match"] = "broad"
    assert first.grain_key != prepare_ad_rows([original])[0].grain_key
