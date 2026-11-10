"""关键词日报小批次幂等写入服务，仅由受控后台调用。

先校验完整候选批次，再在单个 InnoDB 事务内参数化 upsert。相同主键覆盖
指标与名称，不累加消耗；任何写入失败回滚整批。当前限制每批一千行，尚无
跨批任务、历史快照或并发版本冲突仲裁。拒绝带浮点来源警告的候选，不将
未核实金额写入正式表；此入口不向 HTTP 用户开放，也不自动创建账户。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from dashboard.keyword_input import KeywordInputError, KeywordRow, PreparedKeyword

# 固定标识符从共享 Table 派生，不接受输入字段拼接 SQL。
from dashboard.schema import KEYWORD_FIELDS as FIELDS
from dashboard.schema import KEYWORD_KEYS as KEYS

UPSERT = text(
    "INSERT INTO keyword_daily_v2 ("
    + ",".join(f"`{name}`" for name in FIELDS)
    + ") VALUES ("
    + ",".join(f":{name}" for name in FIELDS)
    + ") ON DUPLICATE KEY UPDATE "
    + ",".join(f"`{name}`=:{name}" for name in FIELDS if name not in KEYS)
)


class KeywordWriteError(RuntimeError):
    """不携带 SQL 参数的写入失败提示，不能在上层输出底层异常原文。"""


@dataclass(frozen=True)
class WriteResult:
    """已提交的输入主键数，不把 MySQL affected_rows 当作新增记录数。"""

    processed: int


def validate_keyword_batch(rows: Sequence[PreparedKeyword]) -> None:
    """共用的无 I/O 存储前置校验，供文件预览和正式写入同时使用。"""
    if len(rows) > 1000:
        raise KeywordInputError("每批最多 1000 行，请先拆分并设计跨批任务状态")
    seen = set()
    for index, item in enumerate(rows, start=1):
        if type(item.account_id) is not int or item.account_id <= 0:
            raise KeywordInputError(f"第 {index} 行内部账户无效")
        if item.source_float_hex is not None or item.warnings:
            raise KeywordInputError(f"第 {index} 行金额来源尚未核实，不能正式写入")
        try:
            # 重新验证模型内容，避免通过 model_copy 等方式绕过输入准备阶段。
            KeywordRow.model_validate(item.row.model_dump())
        except ValidationError:
            raise KeywordInputError(f"第 {index} 行内容无效") from None
        if len(item.row.device) > 255 or any(
            len(value.encode("utf-8")) > 65535
            for value in (
                item.row.keyword,
                item.row.campaign_name,
                item.row.adgroup_name,
            )
        ):
            raise KeywordInputError(f"第 {index} 行文本超出存储范围")
        if item.key in seen:
            raise KeywordInputError(f"第 {index} 行主键重复")
        seen.add(item.key)


def write_keyword_rows(conn: Connection, rows: Sequence[PreparedKeyword]) -> None:
    """校验后在调用方事务中写入，不开启、提交或回滚事务。

    直接写入与计划执行共用此入口，统一覆盖规则和锁定次序。调用方必须
    提供事务并让写入异常向外传播，由完整事务负责回滚；本函数不吞异常，
    也不返回已提交行数，因为最终提交仍可能失败。零行不执行 SQL。
    """
    validate_keyword_batch(rows)
    # 固定主键次序减少交叉批次锁顺序冲突，不承诺消除全部死锁。
    for item in sorted(rows, key=lambda value: value.key):
        conn.execute(UPSERT, dict(item.row.model_dump(), account_id=item.account_id))


def store_keyword_batch(engine: Engine, rows: Sequence[PreparedKeyword]) -> WriteResult:
    """提交完整小批次，零行不连接数据库；提交异常可能需读回核对结果。"""
    validate_keyword_batch(rows)
    if not rows:
        return WriteResult(0)
    try:
        with engine.begin() as connection:
            write_keyword_rows(connection, rows)
    except SQLAlchemyError:
        raise KeywordWriteError(
            "批次未确认成功，请按主键读回核对后重试；未回显输入"
        ) from None
    return WriteResult(len(rows))
