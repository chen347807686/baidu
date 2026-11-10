"""文件预览的纯合成测试，无数据库或账号前提。

验证 CSV/JSON 解析、重复行汇总、映射错误、精确金额、重复键和安全输出。
临时文件由 pytest 清理，CLI 测试不执行入库或读取真实业务文件。
"""

import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from dashboard.import_preview import load_rows, main, parse_json, preview_rows


def row(**changes: object) -> dict[str, object]:
    """提供完整的合成行，不依赖其它测试模块或运行数据库。"""
    data: dict[str, object] = {
        name: 0
        for name in (
            "impression",
            "click",
            "conv_form",
            "conv_3sent",
            "conv_lead",
            "conv_1sent",
            "conv_dial",
            "conv_wechat",
            "conv_78",
            "conv_92",
        )
    }
    data.update(
        date="2026-11-09",
        user_name="synthetic",
        campaign_id=1,
        campaign_name="计划",
        adgroup_id=2,
        adgroup_name="单元",
        w_info_id=3,
        keyword="测试词",
        device="pc",
        cost="1.2300",
    )
    return dict(data, **changes)


def test_preview_collects_multiple_problems() -> None:
    """一次展示重复键、缺少映射、非法值，部分合计不能被误认为可入库批次。"""
    result = preview_rows(
        [row(), row(keyword="改名"), row(user_name="unmapped"), row(click=-1)],
        {"synthetic": 1},
    )
    assert (
        result["valid_unique_rows"] == 1 and result["validated_cost_total"] == "1.2300"
    )
    assert result["issues"] == [
        {"row": 2, "code": "duplicate_key", "first_row": 1},
        {"row": 3, "code": "missing_account_mapping"},
        {"row": 4, "code": "invalid_record", "fields": ["click"]},
    ]
    assert not result["structure_valid"] and not result["write_allowed"]
    assert "unmapped" not in json.dumps(result)
    assert not preview_rows([], {"synthetic": 1})["structure_valid"]


def test_file_formats_and_decimal(tmp_path: Path) -> None:
    """CSV 整数字段显式转换，JSON 小数不经 float，所有成功都仍是只读预览。"""
    path = tmp_path / "sample.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row()))
        writer.writeheader()
        writer.writerow(row())
    result = preview_rows(load_rows(path), {"synthetic": 1})
    assert result["structure_valid"] and not result["database_checked"]
    assert parse_json('[{"cost":0.1234}]')[0]["cost"] == Decimal("0.1234")
    json_path = tmp_path / "sample.json"
    json_path.write_text(json.dumps([row()]), encoding="utf-8")
    assert preview_rows(load_rows(json_path), {"synthetic": 1})["structure_valid"]
    assert not preview_rows([row(device="x" * 256)], {"synthetic": 1})[
        "structure_valid"
    ]


@pytest.mark.parametrize("value", ['{"a":1,"a":2}', "[NaN]", "[Infinity]"])
def test_reject_ambiguous_json(value: str) -> None:
    """拒绝解析器可能静默接受的重复键和非有限数。"""
    with pytest.raises(ValueError):
        parse_json(value)


def test_cli_is_read_only_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI 无数据库配置也可运行，失败不输出文件内容或原始解析异常。"""
    source, mapping = tmp_path / "rows.json", tmp_path / "mapping.json"
    source.write_text(json.dumps([row()]), encoding="utf-8")
    mapping.write_text('{"synthetic":1}', encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["preview", "--input", str(source), "--mapping", str(mapping)]
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["write_allowed"] is False
    source.write_text('{"private-content":', encoding="utf-8")
    assert main() == 2
    assert "private-content" not in capsys.readouterr().out


def test_reject_csv_headers_and_large_batches(tmp_path: Path) -> None:
    """重复列和超过单批上限不得被静默截断或接受。"""
    path = tmp_path / "bad.csv"
    path.write_text("date,date\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rows(path)
    with pytest.raises(ValueError):
        preview_rows([row()] * 1001, {"synthetic": 1})
