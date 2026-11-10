"""宽容投放预览的合成验证，不读文件、数据库或真实数据。

覆盖常见表格格式、全部错误汇总、有效行保留、显式时区与浮点选择，确保
易用性改进不偷偷舍入金额、不自动提交部分数据或掩盖重复事实。
"""

from datetime import UTC, datetime
from decimal import Decimal

from dashboard.ad_preview import preview_ad_rows


def row(**changes: object) -> dict[str, object]:
    """提供接近表格导出的最小完整记录，允许省略不适用列。"""
    return {
        "account_id": "1",
        "source_account_id": "2",
        "stat_date": "2026/11/10 12:30:00",
        "report_kind": "keyword",
        "keyword_id": "3",
        "device": "all",
        "device_scope": "summary",
        "impressions": "100",
        "clicks": 10.0,
        "cost": "12.50",
        "source_revision": "r1",
        "source_checked_at": "2026-11-10T12:00:00+08:00",
        "ingested_at": datetime(2026, 11, 10, tzinfo=UTC),
    } | changes


def test_common_formats_and_no_mutation() -> None:
    """常见整数、日期和汇总标识自动处理，所有变换有记录，原始输入不改。"""
    raw = row()
    result = preview_ad_rows([raw])
    assert not result.errors and len(result.valid_rows) == 1
    index, converted = result.valid_rows[0]
    assert index == 1 and converted["device"] is None and converted["clicks"] == 10
    assert raw["device"] == "all" and raw["clicks"] == 10.0
    assert "summary_device_normalized" in {issue.code for issue in result.warnings}


def test_collect_errors_and_keep_valid_rows() -> None:
    """一次列出多处坏行和重复，保留可用行及原始行号，不执行部分写入。"""
    result = preview_ad_rows(
        [row(), row(keyword_id=4, cost="bad"), row(keyword_id=5, clicks=-1), row()]
    )
    assert [number for number, _ in result.valid_rows] == [1]
    assert [issue.row for issue in result.errors] == [2, 3, 4]
    assert result.errors[-1].code == "duplicate_grain"


def test_float_and_timezone_require_explicit_policy() -> None:
    """浮点和缺时区默认拒绝，明确来源规则后可预览但保留核对警告。"""
    raw = row(cost=12.5, source_checked_at="2026-11-10 12:00:00")
    assert preview_ad_rows([raw]).errors
    result = preview_ad_rows([raw], allow_float_cost=True, naive_timezone_hours=8)
    assert not result.errors
    assert result.valid_rows[0][1]["cost"] == Decimal("12.5")
    assert {"float_cost_requires_review", "configured_timezone_applied"} <= {
        issue.code for issue in result.warnings
    }
    assert preview_ad_rows([row(cost=1.234567)], allow_float_cost=True).errors


def test_unsafe_integer_and_unrecognized_device_rejected() -> None:
    """大 ID 浮点精度不可恢复，未知设备不能擅自变成汇总。"""
    assert preview_ad_rows([row(keyword_id=1e20)]).errors
    assert preview_ad_rows([row(device="mystery")]).errors
    assert (
        preview_ad_rows([row(stat_date="PRIVATE_DATE")]).errors[0].code
        == "normalization_failed"
    )
