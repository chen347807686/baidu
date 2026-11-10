"""统一投放日报的纯输入校验与版本化维度指纹。

接收已由来源适配器分离的身份维度、名称和指标，输出可写库参数；无文件、
数据库或网络访问。v1 扩展维度仅支持扁平标量，禁止 float 和嵌套对象；
真实来源接入前仍须核实完整维度，不承诺自动识别上游汇总或遗漏维度。
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

Unsigned = Annotated[int, Field(strict=True, ge=0, le=18446744073709551615)]
Positive = Annotated[int, Field(strict=True, ge=1, le=18446744073709551615)]
Scalar = str | int | bool | None


class AdInputError(ValueError):
    """安全的批次错误，提供记录序号、稳定错误码与已知字段，不携带输入值。"""

    def __init__(self, row: int, code: str, fields: tuple[str, ...] = ()) -> None:
        """保留结构化信息，中文提示可直接用于维护反馈。"""
        self.row, self.code, self.fields = row, code, fields
        super().__init__(f"第 {row} 行投放数据无效：{code}；字段：{', '.join(fields)}")


class AdRow(BaseModel):
    """规范化投放输入；可空指标仍必须显式提供，防止漏列被误当未知。"""

    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, hide_input_in_errors=True
    )
    account_id: Annotated[int, Field(strict=True, ge=1, le=2147483647)]
    source_account_id: Positive
    stat_date: date
    report_kind: Literal["keyword", "search_term", "creative"]
    project_id: Unsigned | None
    campaign_id: Unsigned | None
    adgroup_id: Unsigned | None
    keyword_id: Unsigned | None
    creative_id: Unsigned | None
    keyword_text: str | None
    search_term: str | None
    entity_name: str | None
    device: Annotated[str, Field(max_length=32)] | None
    device_scope: Literal["detail", "summary", "unknown"]
    extra_dimensions: dict[str, Scalar]
    impressions: Unsigned | None
    clicks: Unsigned | None
    cost: Decimal | None
    source_revision: Annotated[str, Field(min_length=1, max_length=64)]
    source_checked_at: datetime
    ingested_at: datetime

    @field_validator("stat_date", mode="before")
    @classmethod
    def parse_day(cls, value: object) -> object:
        """仅接受日期对象或明确 ISO 日期，不接受带时间的 datetime。"""
        if type(value) is str:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("日期必须为 YYYY-MM-DD")
            return parsed
        if isinstance(value, datetime):
            raise ValueError("业务日期不能包含时刻")  # noqa: TRY004 -- Pydantic 用 ValueError 汇总安全校验错误。
        return value

    @field_validator("cost", mode="before")
    @classmethod
    def parse_cost(cls, value: object) -> Decimal | None:
        """直接解析十进制文本与整数，拒绝浮点和超精度，不依赖 decimal 全局精度。"""
        if value is None:
            return None
        if type(value) not in (str, int, Decimal):
            raise ValueError("金额必须为十进制文本")
        try:
            amount = Decimal(str(value))
            if (
                not amount.is_finite()
                or amount < 0
                or amount >= Decimal(100000000000000)
            ):
                raise ValueError("金额超范围")
            # 允许无效尾零，但拒绝任何需要舍入的有效小数位。
            _, digits, exponent = amount.as_tuple()
            assert isinstance(exponent, int)
            trailing = 0
            for digit in reversed(digits):
                if digit != 0:
                    break
                trailing += 1
            if any(digits) and exponent + trailing < -4:
                raise ValueError("金额超精度")
            return Decimal(format(amount, ".4f"))
        except InvalidOperation:
            raise ValueError("金额无效") from None

    @field_validator("source_checked_at", "ingested_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        """必须带时区，转为数据库使用的无时区 UTC，不将本机时间猜为 UTC。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须带时区")
        return value.astimezone(UTC).replace(tzinfo=None)

    @model_validator(mode="after")
    def identity_rules(self) -> "AdRow":
        """要求对应物料身份和明确设备范围；来源设备原始值不做静默改写。"""
        if self.report_kind == "keyword" and self.keyword_id is None:
            raise ValueError("关键词报表缺少关键词 ID")
        if self.report_kind == "creative" and self.creative_id is None:
            raise ValueError("创意报表缺少创意 ID")
        if self.report_kind == "search_term" and not self.search_term:
            raise ValueError("搜索词报表缺少词文本")
        if self.device_scope == "detail" and not self.device:
            raise ValueError("设备明细缺少设备值")
        if self.device_scope == "summary" and self.device is not None:
            raise ValueError("汇总行设备应为空，原始汇总代码放扩展维度")
        for text_value in (self.keyword_text, self.search_term, self.entity_name):
            if text_value is not None and len(text_value.encode("utf-8")) > 65535:
                raise ValueError("文本超存储范围")
        if len(self.extra_dimensions) > 32:
            raise ValueError("扩展维度过多")
        for key, value in self.extra_dimensions.items():
            if not key or len(key.encode("utf-8")) > 128:
                raise ValueError("扩展维度键无效")
            if type(value) not in (str, int, bool, type(None)):
                raise ValueError("扩展维度只接受标量")
            if (
                type(value) is int
                and not -9223372036854775808 <= value <= 18446744073709551615
            ):
                raise ValueError("扩展整数超范围")
            if isinstance(value, str) and len(value.encode("utf-8")) > 2048:
                raise ValueError("扩展维度值过长")
        return self


def canonical(value: object) -> bytes:
    """固定排序、UTF-8 与 JSON 类型，保留 bool/int、空串/NULL/缺失的区别。"""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class PreparedAd:
    """不可变序列化快照，调用方修改取出的字典不会改变已准备的指纹。"""

    payload: bytes
    grain_key: bytes
    content_hash: bytes

    def parameters(self) -> dict[str, object]:
        """返回独立写入参数，恢复 Decimal、日期与 UTC 时间，不执行提交。"""
        values: dict[str, object] = json.loads(self.payload)
        values["grain_key"], values["content_hash"] = self.grain_key, self.content_hash
        values["stat_date"] = date.fromisoformat(str(values["stat_date"]))
        for name in ("source_checked_at", "ingested_at"):
            values[name] = datetime.fromisoformat(str(values[name]))
        if values["cost"] is not None:
            values["cost"] = Decimal(str(values["cost"]))
        return values


def prepare_ad_rows(rows: Sequence[Mapping[str, object]]) -> list[PreparedAd]:
    """全部通过才返回候选，最多一千行；这是准备上限，不是事务写入预算。"""
    if len(rows) > 1000:
        raise AdInputError(0, "batch_too_large")
    result = []
    seen: set[tuple[int, str, str, bytes]] = set()
    for index, raw in enumerate(rows, 1):
        try:
            row = AdRow.model_validate(raw)
            identity = {
                "schema_version": 1,
                "report_kind": row.report_kind,
                "project_id": row.project_id,
                "campaign_id": row.campaign_id,
                "adgroup_id": row.adgroup_id,
                "keyword_id": row.keyword_id,
                "creative_id": row.creative_id,
                "device": row.device,
                "device_scope": row.device_scope,
                "extra": row.extra_dimensions,
                # 搜索词是事实维度；关键词名称和创意名称属于可变展示值。
                "search_term": row.search_term
                if row.report_kind == "search_term"
                else None,
            }
            grain = hashlib.sha256(canonical(identity)).digest()
            key = (row.account_id, row.stat_date.isoformat(), row.report_kind, grain)
            if key in seen:
                raise AdInputError(index, "duplicate_grain")
            seen.add(key)
            values = row.model_dump(mode="json")
            values.pop("extra_dimensions")
            values.update(dimensions=identity, grain_version=1)
            # 内容不含核验/入库时间和来源修订，重复核验不会制造业务变更。
            content = {
                name: value
                for name, value in values.items()
                if name not in {"source_checked_at", "ingested_at", "source_revision"}
            }
            result.append(
                PreparedAd(
                    canonical(values),
                    grain,
                    hashlib.sha256(canonical(content)).digest(),
                )
            )
        except ValidationError as exc:
            fields = tuple(
                sorted(
                    {
                        str(item["loc"][0])
                        for item in exc.errors()
                        if item["loc"] and item["loc"][0] in AdRow.model_fields
                    }
                )
            )
            raise AdInputError(index, "invalid_record", fields) from None
        except (UnicodeError, RecursionError):
            raise AdInputError(index, "invalid_encoding") from None
    return result
