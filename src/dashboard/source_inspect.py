"""本机来源映射与维护事件的有界只读查询。

使用统一维护配置，按递增编号翻页，最多返回一百条；只选择核对所需字段，
不输出名称、凭据引用或秘密。支持按来源稳定身份核对未知创建结果，以及按
映射编号核对版本和事件；仅适用于受信任本机维护者，不是网页授权接口。
"""

import argparse
import json

from sqlalchemy import Connection, select
from sqlalchemy.exc import SQLAlchemyError

from dashboard.database import make_engine
from dashboard.local_config import load_maintenance_database_settings
from dashboard.schema import account_sources, source_events


def inspect_sources(
    conn: Connection,
    *,
    limit: int = 50,
    after_id: int = 0,
    account_id: int | None = None,
    source_id: int | None = None,
    namespace: str | None = None,
    external_id: str | None = None,
) -> list[dict[str, object]]:
    """按稳定编号查询最小映射状态，不选择名称与凭据引用，身份过滤必须成对。"""
    validate_page(limit, after_id)
    if (namespace is None) != (external_id is None):
        raise ValueError("来源身份必须同时提供命名空间和稳定 ID")
    if namespace is not None and (
        not 1 <= len(namespace) <= 64 or not external_id or len(external_id) > 128
    ):
        raise ValueError("来源身份长度无效")
    for value in (account_id, source_id):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError("查询编号无效")
    table = account_sources.c
    query = select(
        table.id,
        table.account_id,
        table.source_namespace,
        table.external_account_id,
        table.active,
        table.mapping_version,
        table.updated_at,
    ).where(table.id > after_id)
    if account_id is not None:
        query = query.where(table.account_id == account_id)
    if source_id is not None:
        query = query.where(table.id == source_id)
    if namespace is not None:
        query = query.where(
            table.source_namespace == namespace,
            table.external_account_id == external_id,
        )
    return [
        dict(row)
        for row in conn.execute(query.order_by(table.id).limit(limit)).mappings()
    ]


def validate_page(limit: int, after_id: int) -> None:
    """拒绝隐式整数类型与无界查询，编号仅作为继续向后翻页的游标。"""
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(after_id) is not int
        or not 0 <= after_id <= 18446744073709551615
    ):
        raise ValueError("分页参数无效")


def inspect_events(
    conn: Connection, source_id: int, *, limit: int = 50, after_id: int = 0
) -> list[dict[str, object]]:
    """按映射与事件编号读取维护留痕，不返回原业务值；空结果不代表映射存在。"""
    validate_page(limit, after_id)
    if type(source_id) is not int or not 1 <= source_id <= 18446744073709551615:
        raise ValueError("映射编号无效")
    table = source_events.c
    query = (
        select(
            table.id,
            table.source_id,
            table.action,
            table.mapping_version,
            table.operator,
            table.changed_fields,
            table.created_at,
        )
        .where(table.source_id == source_id, table.id > after_id)
        .order_by(table.id)
        .limit(limit)
    )
    return [dict(row) for row in conn.execute(query).mappings()]


def main() -> int:
    """执行只读维护查询；不要求写入确认，失败不回显原始数据库异常。"""
    parser = argparse.ArgumentParser(description="本机来源映射只读核对")
    parser.add_argument("action", choices=["list", "events"])
    parser.add_argument("--source-id", type=int)
    parser.add_argument("--account-id", type=int)
    parser.add_argument("--namespace")
    parser.add_argument("--external-id")
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    engine = None
    try:
        # 在读取维护凭据前校验基本参数，避免错误请求触碰数据库。
        validate_page(args.limit, args.after_id)
        if (args.namespace is None) != (args.external_id is None):
            raise ValueError("来源身份必须成对")
        if args.action == "events" and (
            args.source_id is None
            or args.account_id is not None
            or args.namespace is not None
        ):
            raise ValueError("事件查询仅接受映射编号")
        engine = make_engine(load_maintenance_database_settings())
        with engine.connect() as conn:
            # MySQL 事务级只读保护，不以函数名称代替真正的写入限制。
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            if args.action == "events":
                rows = inspect_events(
                    conn, args.source_id, limit=args.limit, after_id=args.after_id
                )
            else:
                rows = inspect_sources(
                    conn,
                    limit=args.limit,
                    after_id=args.after_id,
                    source_id=args.source_id,
                    account_id=args.account_id,
                    namespace=args.namespace,
                    external_id=args.external_id,
                )
        # 返回尾编号，下一页仍用相同过滤条件；空页表示当前没有后续记录。
        print(
            json.dumps(
                {"rows": rows, "next_after_id": rows[-1]["id"] if rows else None},
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    except (ValueError, SQLAlchemyError):
        print("只读核对失败，请检查参数、本机配置及迁移；未输出底层异常。")
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
