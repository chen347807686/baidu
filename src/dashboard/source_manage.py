"""本机受控来源账户维护，不提供 HTTP 写入口。

使用统一维护配置，显式 create/update 并要求确认；调用服务接收 Connection，
映射与事件在外层事务共同提交。稳定身份和内部绑定建立后不可直接改动，
更新需匹配预期版本以防覆盖他人变更；本地维护者不等于认证的业务用户。
不显示映射内容或凭据，不保证生产审计不可篡改，不自动创建内部账户。
"""

import argparse
import getpass
import re

from sqlalchemy import Connection, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from dashboard.auth import utc_now
from dashboard.database import make_engine
from dashboard.local_config import load_maintenance_database_settings
from dashboard.schema import account_sources, accounts, source_events


def maintain_source(
    conn: Connection,
    *,
    operator: str,
    account_id: int | None = None,
    namespace: str | None = None,
    external_id: str | None = None,
    name: str | None = None,
    source_id: int | None = None,
    expected_version: int | None = None,
    active: bool | None = None,
) -> tuple[int, int]:
    """校验后创建或按版本更新；无变化不增加版本或事件，事务由调用方负责。"""
    if not 1 <= len(operator) <= 100 or (
        name is not None and not 1 <= len(name) <= 255
    ):
        raise ValueError("维护者或名称无效")
    if active is not None and type(active) is not bool:
        raise ValueError("启用状态无效")
    now = utc_now()
    if source_id is None:
        if type(account_id) is not int or not 1 <= account_id <= 2147483647 or not name:
            raise ValueError("内部账户或名称无效")
        if not namespace or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", namespace):
            raise ValueError("来源命名空间无效")
        if not external_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", external_id):
            raise ValueError("来源稳定 ID 无效")
        if expected_version is not None:
            raise ValueError("创建不能指定旧版本")
        if (
            conn.execute(
                select(accounts.c.id)
                .where(accounts.c.id == account_id)
                .with_for_update()
            ).first()
            is None
        ):
            raise ValueError("内部账户不存在")
        result = conn.execute(
            insert(account_sources).values(
                account_id=account_id,
                source_namespace=namespace,
                external_account_id=external_id,
                source_account_name=name,
                channel="paid_baidu_search",
                active=active if active is not None else True,
                mapping_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("来源映射创建后未取得编号")
        source_id = int(primary_key[0])
        version = 1
        fields = [
            "account_id",
            "source_namespace",
            "external_account_id",
            "source_account_name",
            "active",
        ]
        action = "create"
    else:
        if (
            type(source_id) is not int
            or source_id <= 0
            or type(expected_version) is not int
            or expected_version < 1
        ):
            raise ValueError("映射编号或预期版本无效")
        if account_id is not None or namespace is not None or external_id is not None:
            raise ValueError("稳定来源身份与内部绑定不可直接修改")
        row = (
            conn.execute(
                select(account_sources)
                .where(account_sources.c.id == source_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None or row["mapping_version"] != expected_version:
            raise ValueError("映射不存在或版本已变化")
        changes: dict[str, object] = {}
        if name is not None and name != row["source_account_name"]:
            changes["source_account_name"] = name
        if active is not None and active != row["active"]:
            changes["active"] = active
        if not changes:
            return source_id, expected_version
        fields = sorted(changes)
        version = expected_version + 1
        conn.execute(
            update(account_sources)
            .where(account_sources.c.id == source_id)
            .values(**changes, mapping_version=version, updated_at=now)
        )
        action = "update"
    conn.execute(
        insert(source_events).values(
            source_id=source_id,
            action=action,
            mapping_version=version,
            operator=operator,
            changed_fields=fields,
            created_at=now,
        )
    )
    return source_id, version


def main() -> int:
    """显式确认后使用本机维护权限提交，不输出底层错误或账户内容。"""
    parser = argparse.ArgumentParser(description="本机来源账户维护")
    parser.add_argument("action", choices=["create", "update"])
    parser.add_argument("--account-id", type=int)
    parser.add_argument("--namespace")
    parser.add_argument("--external-id")
    parser.add_argument("--name")
    parser.add_argument("--source-id", type=int)
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--active", choices=["yes", "no"])
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if (
        not args.confirm
        or (args.action == "create" and args.source_id is not None)
        or (args.action == "update" and args.source_id is None)
    ):
        print("请检查操作编号并显式添加 --confirm；未连接数据库。")
        return 2
    engine = None
    try:
        engine = make_engine(load_maintenance_database_settings())
        with engine.begin() as conn:
            identifier, version = maintain_source(
                conn,
                operator=getpass.getuser(),
                account_id=args.account_id,
                namespace=args.namespace,
                external_id=args.external_id,
                name=args.name,
                source_id=args.source_id,
                expected_version=args.expected_version,
                active=None if args.active is None else args.active == "yes",
            )
    except (ValueError, SQLAlchemyError):
        print(
            "维护未确认成功，请检查输入、迁移、唯一身份及版本；提交异常需核对映射与事件，未输出业务内容。"
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()
    print(f"维护完成：映射编号 {identifier}，版本 {version}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
