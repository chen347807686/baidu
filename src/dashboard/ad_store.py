"""统一投放事实的受控小批写入，不提供 HTTP 或来源采集入口。

重新准备原始规范输入，在自有事务内锁定账户、来源映射和已有事实，检查
映射版本及核验顺序后覆盖而非累加。最多十行事实写入，失败整批回滚；
不自动重试、不推进同步水位或生成投影，提交断线后仍须按事实键读回核对。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from dashboard.ad_input import prepare_ad_rows
from dashboard.database_diagnostics import report_database_error
from dashboard.schema import account_sources, accounts, ad_daily


class AdWriteError(ValueError):
    """不携带来源值的写入拒绝；调用方不能据此盲目重试。"""


@dataclass(frozen=True)
class AdWriteResult:
    """实际插入、更新及重复未变行数，返回时自有事务已提交。"""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def store_ad_batch(
    engine: Engine,
    rows: Sequence[Mapping[str, object]],
    expected_versions: Mapping[int, int],
) -> AdWriteResult:
    """提交最多十行事实；映射版本必须精确覆盖本批使用的来源，不接受隐式类型。

    所有候选先重新计算指纹，调用方不能传入自造指纹绕过校验。账户锁用于
    串行化同账户的新事实插入；后续写入口必须遵守同一锁顺序。此处只保证
    行数上限，不保证一秒截止时间；同步事件/断点接入后需重新分配事务预算。
    """
    if len(rows) > 10:
        raise AdWriteError("每次最多写入十行投放事实")
    parameters = [item.parameters() for item in prepare_ad_rows(rows)]
    source_ids = {int(str(item["source_account_id"])) for item in parameters}
    if (
        any(
            type(key) is not int or type(value) is not int or value < 1
            for key, value in expected_versions.items()
        )
        or set(expected_versions) != source_ids
    ):
        raise AdWriteError("必须提供本批全部来源的有效预期版本")
    if not parameters:
        return AdWriteResult()
    parameters.sort(
        key=lambda item: (
            int(str(item["account_id"])),
            str(item["stat_date"]),
            str(item["report_kind"]),
            str(item["grain_key"]),
        )
    )
    inserted = updated = unchanged = 0
    try:
        with engine.begin() as conn:
            account_ids = sorted({int(str(item["account_id"])) for item in parameters})
            locked_accounts = conn.execute(
                select(accounts.c.id)
                .where(accounts.c.id.in_(account_ids))
                .order_by(accounts.c.id)
                .with_for_update()
            ).all()
            if len(locked_accounts) != len(account_ids):
                raise AdWriteError("内部账户不存在")
            sources = {
                row["id"]: row
                for row in conn.execute(
                    select(account_sources)
                    .where(account_sources.c.id.in_(sorted(source_ids)))
                    .order_by(account_sources.c.id)
                    .with_for_update()
                ).mappings()
            }
            if len(sources) != len(source_ids):
                raise AdWriteError("来源映射不存在")
            for item in parameters:
                source = sources[item["source_account_id"]]
                if (
                    source["account_id"] != item["account_id"]
                    or not source["active"]
                    or source["mapping_version"] != expected_versions[source["id"]]
                ):
                    raise AdWriteError("来源绑定、启用状态或版本已变化")
                conditions = [
                    column == item[column.name]
                    for column in ad_daily.primary_key.columns
                ]
                existing = (
                    conn.execute(select(ad_daily).where(*conditions).with_for_update())
                    .mappings()
                    .first()
                )
                if existing is None:
                    conn.execute(insert(ad_daily), item)
                    inserted += 1
                    continue
                # 指纹相同仍比较完整维度，防碰撞、规范版本漂移及来源交叉覆盖。
                if (
                    existing["dimensions"] != item["dimensions"]
                    or existing["grain_version"] != item["grain_version"]
                    or existing["source_account_id"] != item["source_account_id"]
                ):
                    raise AdWriteError("事实身份或来源冲突，需核对完整维度")
                checked = item["source_checked_at"]
                assert isinstance(checked, datetime)
                if checked < existing["source_checked_at"]:
                    raise AdWriteError("旧核验结果不能覆盖新事实")
                if checked == existing["source_checked_at"]:
                    if (
                        existing["content_hash"] != item["content_hash"]
                        or existing["source_revision"] != item["source_revision"]
                    ):
                        raise AdWriteError("同一核验时刻存在不同内容或修订，需人工核对")
                    unchanged += 1
                    continue
                # 新核验允许 NULL 覆盖旧值，不能用 COALESCE 留住过期指标。
                conn.execute(update(ad_daily).where(*conditions).values(**item))
                updated += 1
    except SQLAlchemyError as exc:
        hint = report_database_error(exc, "ad_batch")
        raise AdWriteError(
            hint + "；提交未确认，请按事实键读回核对，未输出输入"
        ) from None
    return AdWriteResult(inserted, updated, unchanged)
