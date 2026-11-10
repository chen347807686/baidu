"""日报准备入口的纯合成测试，不依赖数据库或本地登录账号。

覆盖主键粒度、未知转化保留、显式账户映射、严格整数、金额精度和浮点来源。
调用过程无文件或数据库写入，因此失败批次不会产生部分导入数据。
"""

from decimal import Decimal

import pytest

from dashboard.keyword_input import KeywordInputError, prepare_keyword_rows


def sample(**changes: object) -> dict[str, object]:
    """生成字段齐全的合成日报；覆盖参数只影响本次测试样本。"""
    row: dict[str, object] = {
        "date": "2026-11-09",
        "user_name": "synthetic",
        "campaign_id": 1,
        "campaign_name": "计划",
        "adgroup_id": 2,
        "adgroup_name": "单元",
        "w_info_id": 3,
        "keyword": "同词",
        "device": "pc",
        "impression": 10,
        "click": 2,
        "cost": "1.2300",
        "conv_form": 1,
        "conv_3sent": 0,
        "conv_lead": 0,
        "conv_1sent": 1,
        "conv_dial": 0,
        "conv_wechat": 0,
        "conv_78": 7,
        "conv_92": 9,
    }
    return dict(row, **changes)


def test_grain_and_original_conversions() -> None:
    """同词不同 ID 或设备必须保留为不同事实行，未知转化值不丢失。"""
    rows = prepare_keyword_rows(
        [sample(), sample(w_info_id=4), sample(device="mobile")], {"synthetic": 1}
    )
    assert len({item.key for item in rows}) == 3
    assert rows[0].row.conv_78 == 7 and rows[0].row.conv_92 == 9
    assert rows[0].row.cost == Decimal("1.2300")
    with pytest.raises(KeywordInputError, match="主键重复"):
        prepare_keyword_rows([sample(), sample(keyword="改名")], {"synthetic": 1})


@pytest.mark.parametrize(
    "changes",
    [
        {"click": True},
        {"w_info_id": "3"},
        {"click": -1},
        {"cost": "1.23456"},
        {"cost": "NaN"},
        {"date": "not-a-date"},
        {"secret": "must-not-appear"},
    ],
)
def test_invalid_values_are_not_silently_converted(changes: dict[str, object]) -> None:
    """异常输入必须拒绝，错误不能含原始输入值。"""
    with pytest.raises(KeywordInputError) as caught:
        prepare_keyword_rows([sample(**changes)], {"synthetic": 1})
    assert "must-not-appear" not in str(caught.value)


def test_float_requires_review_and_explicit_mapping() -> None:
    """旧浮点显式允许后仍有来源警告，缺少映射不能自动创建账户。"""
    with pytest.raises(KeywordInputError):
        prepare_keyword_rows([sample(cost=1.23)], {"synthetic": 1})
    item = prepare_keyword_rows(
        [sample(cost=1.23)], {"synthetic": 1}, allow_legacy_float=True
    )[0]
    assert item.source_float_hex == (1.23).hex() and item.warnings
    with pytest.raises(KeywordInputError, match="缺少账户映射"):
        prepare_keyword_rows([sample()], {})
    with pytest.raises(KeywordInputError, match="唯一的正整数"):
        prepare_keyword_rows([sample()], {"synthetic": True})
