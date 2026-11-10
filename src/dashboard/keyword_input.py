"""旧关键词日报的纯校验与导入准备入口。

只接收调用方已提供的行和显式账户映射，不读取文件、旧库或真实凭据，不执行
SQL。保留关键词 ID、设备及八种转化字段，按旧版主键发现批内重复。金额拒绝
隐式舍入；旧浮点数仅在显式允许的核对模式下转为候选十进制值，并保留原表示
和警告，不能据此宣称原始金额无损。正式入库和快照来源后续独立实现。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Count = Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]


class KeywordInputError(ValueError):
    """只携带行号和字段名的安全错误，不包含账户名称或整行原始数据。"""


class KeywordRow(BaseModel):
    """旧 SQLite keyword_daily 的完整行契约，缺列或未知列均拒绝。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    date: date
    user_name: Annotated[str, Field(min_length=1, max_length=255)]
    campaign_id: Count
    campaign_name: str
    adgroup_id: Count
    adgroup_name: str
    w_info_id: Count
    keyword: Annotated[str, Field(min_length=1)]
    device: str
    impression: Count
    click: Count
    cost: Annotated[
        Decimal, Field(ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)
    ]
    conv_form: Count
    conv_3sent: Count
    conv_lead: Count
    conv_1sent: Count
    conv_dial: Count
    conv_wechat: Count
    conv_78: Count
    conv_92: Count


@dataclass(frozen=True)
class PreparedKeyword:
    """带显式内部账户和金额溯源信息的候选行，不代表已写入数据库。"""

    row: KeywordRow
    account_id: int
    source_float_hex: str | None
    warnings: tuple[str, ...]

    @property
    def key(self) -> tuple[date, int, int, int, int, str]:
        """按已核实的日事实粒度构造键，名称变化不改变对象身份。"""
        r = self.row
        return (
            r.date,
            self.account_id,
            r.campaign_id,
            r.adgroup_id,
            r.w_info_id,
            r.device,
        )


def prepare_keyword_rows(
    rows: Sequence[Mapping[str, object]],
    account_mapping: Mapping[str, int],
    *,
    allow_legacy_float: bool = False,
) -> list[PreparedKeyword]:
    """全部校验通过才返回候选批次；不自动建账户，不做静默覆盖或四舍五入。"""
    # 映射必须一对一，防止不同源账户意外归并到同一内部账户。
    ids = list(account_mapping.values())
    if any(type(value) is not int or value <= 0 for value in ids) or len(
        set(ids)
    ) != len(ids):
        raise KeywordInputError("账户映射必须使用唯一的正整数 ID")
    prepared: list[PreparedKeyword] = []
    seen: set[tuple[date, int, int, int, int, str]] = set()
    for index, raw in enumerate(rows, start=1):
        values = dict(raw)
        source_float_hex = None
        warnings: tuple[str, ...] = ()
        try:
            # 只解析明确 ISO 日期；不修剪词文本、设备或账户名，避免改变主键。
            if type(values.get("date")) is str:
                values["date"] = date.fromisoformat(str(values["date"]))
            cost = values.get("cost")
            if type(cost) is float:
                if not allow_legacy_float:
                    raise ValueError("cost")
                source_float_hex = cost.hex()
                warnings = ("旧浮点金额仅作核对候选，原始金额精度尚未确认",)
                values["cost"] = Decimal(repr(cost))
            elif type(cost) in (str, int):
                values["cost"] = Decimal(str(cost))
            row = KeywordRow.model_validate(values)
        except ValidationError as exc:
            fields = ", ".join(sorted({str(error["loc"][0]) for error in exc.errors()}))
            raise KeywordInputError(f"第 {index} 行字段无效：{fields}") from None
        except (ValueError, ArithmeticError):
            raise KeywordInputError(f"第 {index} 行日期或金额无效") from None
        if row.user_name not in account_mapping:
            raise KeywordInputError(f"第 {index} 行缺少账户映射")
        item = PreparedKeyword(
            row, account_mapping[row.user_name], source_float_hex, warnings
        )
        if item.key in seen:
            raise KeywordInputError(f"第 {index} 行主键重复，请先核对快照和回补来源")
        seen.add(item.key)
        prepared.append(item)
    return prepared
