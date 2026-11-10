"""关键词日报文件的本地只读预览工具。

读取调用者显式指定的 UTF-8 CSV/JSON 文件和 JSON 账户映射，最多一千行、
每文件五 MiB。汇总校验错误而非遇首错即退出，不输出源账户名、词文本或
原始错误内容。默认不连接数据库；显式 --database 才做本机只读核对，任何
模式都不执行写入。预览通过不等于来源金额已核实或已获得入库授权。
"""

import argparse
import csv
import io
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from dashboard.keyword_input import (
    KeywordInputError,
    KeywordRow,
    PreparedKeyword,
    prepare_keyword_rows,
)
from dashboard.keyword_store import validate_keyword_batch

MAX_BYTES = 5 * 1024 * 1024
INTEGER_FIELDS = {
    "campaign_id",
    "adgroup_id",
    "w_info_id",
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
}


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝重复 JSON 键，不接受解析器静默保留最后值的行为。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象包含重复字段")
        result[key] = value
    return result


def read_text(path: Path) -> str:
    """限量读取并兼容 UTF-8 BOM，不读取超大文件或回显文件内容。"""
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("文件超过 5 MiB")
    return data.decode("utf-8-sig")


def parse_json(text: str) -> Any:
    """十进制字面量直接解析为 Decimal，避免新增二进制浮点误差。"""

    def reject_constant(value: str) -> None:
        """拒绝 NaN/Infinity 等非标准 JSON 数值。"""
        raise ValueError("JSON 非法数值")

    return json.loads(
        text,
        parse_float=Decimal,
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )


def load_rows(path: Path) -> list[Mapping[str, object]]:
    """按扩展名读取结构化记录；CSV 计数仅允许十进制非负整数文本。"""
    content = read_text(path)
    if path.suffix.lower() == ".json":
        rows = parse_json(content)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("JSON 必须是行对象数组")
    elif path.suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(content), strict=True)
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers) or set(headers) != set(
            KeywordRow.model_fields
        ):
            raise ValueError("CSV 表头重复、缺列或含未知列")
        rows = []
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("CSV 行列数不一致")
            row: dict[str, object] = dict(raw)
            for field in INTEGER_FIELDS:
                value = raw[field]
                if re.fullmatch(r"[0-9]{1,19}", value):
                    row[field] = int(value)
            rows.append(row)
            if len(rows) > 1000:
                raise ValueError("每次预览最多 1000 行")
    else:
        raise ValueError("仅支持 CSV 或 JSON")
    if len(rows) > 1000:
        raise ValueError("每次预览最多 1000 行")
    return rows


def preview_rows(
    rows: Sequence[Mapping[str, object]], mapping: Mapping[str, int]
) -> dict[str, Any]:
    """逐行聚合错误，行号指数据记录序号；统计仅包含校验通过的唯一记录。"""
    prepare_keyword_rows([], mapping)
    if len(rows) > 1000:
        raise ValueError("每次预览最多 1000 行")
    seen: dict[tuple[date, int, int, int, int, str], int] = {}
    valid: list[PreparedKeyword] = []
    issues: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        try:
            item = prepare_keyword_rows([row], mapping)[0]
            validate_keyword_batch([item])
            if item.key in seen:
                issues.append(
                    {"row": index, "code": "duplicate_key", "first_row": seen[item.key]}
                )
                continue
            seen[item.key] = index
            valid.append(item)
        except KeywordInputError as exc:
            # 不复制底层提示中的任意输入字段名，避免恶意列名泄露到预览报告。
            code = (
                "missing_account_mapping"
                if "缺少账户映射" in str(exc)
                else "invalid_record"
            )
            issue: dict[str, object] = {"row": index, "code": code}
            if "字段无效：" in str(exc):
                # 只展示已知模型字段名，任意外来列名不复制到输出。
                issue["fields"] = [
                    name
                    for name in str(exc).split("字段无效：", 1)[1].split(", ")
                    if name in KeywordRow.model_fields
                ]
            elif "日期或金额无效" in str(exc):
                issue["fields"] = ["date", "cost"]
            issues.append(issue)
    return {
        "mode": "preview_only",
        "rows": len(rows),
        "valid_unique_rows": len(valid),
        "issue_count": len(issues),
        "issues": issues,
        "validated_cost_total": str(sum((item.row.cost for item in valid), Decimal(0))),
        "structure_valid": bool(rows) and not issues,
        "database_checked": False,
        "write_allowed": False,
        "notice": "仅完成文件结构校验；账户存在性、库内冲突、金额来源尚未核实，未写入数据库",
    }


def main() -> int:
    """输出安全 JSON 摘要；结构错误返回 2，行校验问题返回 1，通过返回 0。"""
    parser = argparse.ArgumentParser(description="关键词日报只读导入预览，不执行入库")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument(
        "--database", action="store_true", help="显式启用本机数据库只读对比，不写入"
    )
    args = parser.parse_args()
    try:
        mapping = parse_json(read_text(args.mapping))
        if not isinstance(mapping, dict):
            raise TypeError("账户映射必须是 JSON 对象")
        rows = load_rows(args.input)
        result = preview_rows(rows, mapping)
        if args.database and result["structure_valid"]:
            # 按需加载数据库依赖；默认纯文件预览不读取任何数据库配置。
            from sqlalchemy.exc import SQLAlchemyError

            from dashboard.database import load_database_settings, make_engine
            from dashboard.import_compare import compare_keyword_batch

            settings = load_database_settings()
            if (
                settings.host != "127.0.0.1"
                or settings.port != 13306
                or settings.name != "onetitle_v2_local"
            ):
                raise ValueError("命令行只允许本机 V2 验证库")
            engine = make_engine(settings)
            try:
                result["database_preview"] = compare_keyword_batch(
                    engine, prepare_keyword_rows(rows, mapping)
                )
                result["database_checked"] = True
                result["notice"] = (
                    "文件及数据库只读对比完成；金额来源和执行确认尚未完成，未写入数据库"
                )
            except SQLAlchemyError:
                print(
                    json.dumps(
                        {
                            "error": "数据库只读核对失败，请检查连接、权限及迁移状态；未执行入库"
                        },
                        ensure_ascii=False,
                    )
                )
                return 2
            finally:
                engine.dispose()
        elif args.database:
            result["database_skipped_reason"] = "文件校验未通过，未连接数据库"
    except (OSError, ValueError, TypeError, ArithmeticError, csv.Error, RecursionError):
        print(
            json.dumps(
                {
                    "error": "文件、映射或编码无效，请检查格式、大小及必填列；未写入数据库"
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("database_preview", {}).get("counts", {}).get(
        "missing_account", 0
    ) or result.get("database_preview", {}).get("counts", {}).get(
        "account_alias_review", 0
    ):
        return 1
    return 0 if result["structure_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
