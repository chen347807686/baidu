"""拓词契约和模拟器测试，不连接数据库或真实模型。"""

import pytest

from dashboard.keyword_expansion import ExpansionSeed, simulate_expansion
from dashboard.keyword_negative_rules import NegativeRuleProfile


def seed(**changes: object) -> ExpansionSeed:
    values = {"seed_id": "seed-1", "account_id": 7, "keyword": "黄金回收", "quality": "high", "negative_status": "clear"}
    values.update(changes)
    return ExpansionSeed(**values)


def test_simulation_keeps_source_keyword_and_account() -> None:
    rows = simulate_expansion(seed(), profile=NegativeRuleProfile.from_mapping())
    assert len(rows) == 2
    assert {row.source_keyword for row in rows} == {"黄金回收"}
    assert {row.account_id for row in rows} == {7}


@pytest.mark.parametrize("changes", [{"quality": "ordinary"}, {"negative_status": "confirmed"}])
def test_ineligible_seed_is_rejected(changes: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        simulate_expansion(seed(**changes), profile=NegativeRuleProfile.from_mapping())


def test_candidates_receive_negative_rule_result() -> None:
    profile = NegativeRuleProfile.from_mapping()
    rows = simulate_expansion(seed(), profile=profile, custom_negatives=("黄金回收",))
    assert all(row.negative_matched for row in rows)
    assert all(row.source_keyword == "黄金回收" for row in rows)
