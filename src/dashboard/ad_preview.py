"""投放导入的宽容准备层，聚合问题而不遇首错停止。

转换常见表格格式后交给严格事实校验，返回可用行、原行号及所有错误/警告。
不写数据库，不自动提交部分结果；浮点金额与缺时区需要明确配置，所有转换
有固定提示，不输出原始值。来源字段映射与嵌套维度仍由来源契约明确。
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from dashboard.ad_input import AdInputError, PreparedAd, prepare_ad_rows


@dataclass(frozen=True)
class ImportIssue:
    """可直接展示的行级问题，不包含来源值或任意外来字段名。"""

    row: int
    code: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdPreview:
    """预览保留有效候选，正式提交需调用方明确选择并重新校验。"""

    valid_rows: tuple[tuple[int, dict[str, object]], ...]
    errors: tuple[ImportIssue, ...]
    warnings: tuple[ImportIssue, ...]


def preview_ad_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    allow_float_cost: bool = False,
    naive_timezone_hours: int | None = None,
) -> AdPreview:
    """一次收集全部行错误；显式允许浮点时按 repr 转十进制，绝不自动舍入。

    缺时区仅在指定固定来源时区后解释；日期带时间取其本地业务日期，不转
    UTC 再截日。可选文本/实体漏列补 None，但身份、指标和来源时间不猜测。
    """
    if len(rows) > 1000:
        raise AdInputError(0, "batch_too_large")
    if naive_timezone_hours is not None and (
        type(naive_timezone_hours) is not int or not -12 <= naive_timezone_hours <= 14
    ):
        raise ValueError("来源时区必须为 -12 至 14 的整数小时")
    valid: list[tuple[int, dict[str, object]]] = []
    errors: list[ImportIssue] = []
    warnings: list[ImportIssue] = []
    seen: set[tuple[object, object, object, bytes]] = set()
    for index, raw in enumerate(rows, 1):
        values = dict(raw)
        notes: list[ImportIssue] = []

        def note(
            code: str,
            field: str,
            *,
            target: list[ImportIssue] = notes,
            number: int = index,
        ) -> None:
            """仅从内部固定字段生成转换记录，不复制输入内容。"""
            target.append(ImportIssue(number, code, (field,)))

        try:
            for field in (
                "project_id",
                "campaign_id",
                "adgroup_id",
                "keyword_id",
                "creative_id",
                "keyword_text",
                "search_term",
                "entity_name",
                "device",
            ):
                if field not in values:
                    values[field] = None
                    note("missing_optional_as_null", field)
            if "extra_dimensions" not in values:
                values["extra_dimensions"] = {}
            # 表格常见的整数字符串不应要求对接方重复实现转换。
            for field in (
                "account_id",
                "source_account_id",
                "project_id",
                "campaign_id",
                "adgroup_id",
                "keyword_id",
                "creative_id",
                "impressions",
                "clicks",
            ):
                value = values.get(field)
                if isinstance(value, str) and re.fullmatch(
                    r"[0-9]{1,20}", value.strip()
                ):
                    values[field] = int(value.strip())
                    note("integer_text_converted", field)
                elif (
                    type(value) is float
                    and math.isfinite(value)
                    and value.is_integer()
                    and 0 <= value <= 9007199254740991
                ):
                    values[field] = int(value)
                    note("integer_float_converted", field)
            day = values.get("stat_date")
            if isinstance(day, datetime):
                values["stat_date"] = day.date()
                note("datetime_as_business_date", "stat_date")
            elif isinstance(day, str):
                text = day.strip().replace("/", "-")
                try:
                    parsed = date.fromisoformat(text)
                except ValueError:
                    parsed = datetime.fromisoformat(text).date()
                values["stat_date"] = parsed
                if parsed.isoformat() != day:
                    note("date_text_normalized", "stat_date")
            for field in ("source_checked_at", "ingested_at"):
                value = values.get(field)
                if isinstance(value, str):
                    value = datetime.fromisoformat(value.strip())
                    values[field] = value
                    note("timestamp_text_converted", field)
                if (
                    isinstance(value, datetime)
                    and value.utcoffset() is None
                    and naive_timezone_hours is not None
                ):
                    values[field] = value.replace(
                        tzinfo=timezone(timedelta(hours=naive_timezone_hours))
                    )
                    note("configured_timezone_applied", field)
            cost = values.get("cost")
            if type(cost) is float and math.isfinite(cost) and allow_float_cost:
                values["cost"] = Decimal(repr(cost))
                note("float_cost_requires_review", "cost")
            # 仅对显式汇总范围处理常见标识，不把未知设备猜为汇总。
            if values.get("device_scope") == "summary" and values.get("device") in (
                "",
                "all",
                "全部",
                "汇总",
            ):
                values["device"] = None
                note("summary_device_normalized", "device")
            prepared: PreparedAd = prepare_ad_rows([values])[0]
            params = prepared.parameters()
            key = (
                params["account_id"],
                params["stat_date"],
                params["report_kind"],
                prepared.grain_key,
            )
            if key in seen:
                raise AdInputError(index, "duplicate_grain")
            seen.add(key)
            valid.append((index, values))
        except AdInputError as exc:
            errors.append(ImportIssue(index, exc.code, exc.fields))
        except (ValueError, OverflowError, TypeError):
            errors.append(ImportIssue(index, "normalization_failed"))
        warnings.extend(notes)
    return AdPreview(tuple(valid), tuple(errors), tuple(warnings))
