"""正式日报的数据库只读对比，不执行确认或入库。

在同一 REPEATABLE READ 只读事务中核对账户与候选主键，分为新增、变化、
未变化和需核对。只输出行号与变化字段名，不输出词文本、账户名或旧值。
源账户名称与已有内部账户记录不一致时仅标记待核对，不决定改名归并语义。
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, column, select, table, tuple_

from dashboard.keyword_input import PreparedKeyword
from dashboard.keyword_store import FIELDS, KEYS, validate_keyword_batch

facts = table("keyword_daily_v2", *[column(name) for name in FIELDS])
accounts = table("access_accounts", column("id"))


def compare_keyword_batch(
    engine: Engine, rows: Sequence[PreparedKeyword]
) -> dict[str, Any]:
    """对候选执行只读核对；结果只代表当前快照，不是执行授权或锁定承诺。"""
    validate_keyword_batch(rows)
    counts = {
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "missing_account": 0,
        "account_alias_review": 0,
    }
    details: list[dict[str, Any]] = []
    if not rows:
        return {
            "counts": counts,
            "details": details,
            "database_checked": False,
            "write_allowed": False,
        }
    ids = {item.account_id for item in rows}
    existing: dict[tuple[object, ...], dict[str, Any]] = {}
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        # MySQL 下一事务设为只读；首个一致性 SELECT 建立快照，结束不提交写入。
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        present: set[int] = set(
            conn.execute(select(accounts.c.id).where(accounts.c.id.in_(ids))).scalars()
        )
        alias_review: set[int] = set()
        source_by_account = {item.account_id: item.row.user_name for item in rows}
        for account, source in source_by_account.items():
            conflict = conn.execute(
                select(facts.c.account_id)
                .where(
                    facts.c.account_id == account,
                    facts.c.user_name.collate("utf8mb4_0900_bin") != source,
                )
                .limit(1)
            ).first()
            if conflict is not None:
                alias_review.add(account)
        # 单次最多一百主键，避免单条 SQL 参数数量随批次无界增长。
        for offset in range(0, len(rows), 100):
            keys = [item.key for item in rows[offset : offset + 100]]
            query = select(facts).where(
                tuple_(*[facts.c[name] for name in KEYS]).in_(keys)
            )
            for record in conn.execute(query).mappings():
                value = dict(record)
                existing[tuple(value[name] for name in KEYS)] = value
        for index, item in enumerate(rows, start=1):
            detail: dict[str, Any] = {"row": index}
            if item.account_id not in present:
                status = "missing_account"
            elif item.account_id in alias_review:
                status = "account_alias_review"
            elif item.key not in existing:
                status = "new"
            else:
                expected = dict(item.row.model_dump(), account_id=item.account_id)
                changed = [
                    name
                    for name in FIELDS
                    if existing[item.key][name] != expected[name]
                ]
                status = "changed" if changed else "unchanged"
                detail["changed_fields"] = changed
            counts[status] += 1
            detail["status"] = status
            details.append(detail)
    return {
        "counts": counts,
        "details": details,
        "database_checked": True,
        "write_allowed": False,
        "notice": "只读快照对比完成；源账户改名和金额来源须核实，未确认或执行入库",
    }
