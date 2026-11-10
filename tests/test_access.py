"""在真实本机 MySQL 中验证可配置账户权限。

测试迁移完成后在事务中写入合成人员、部门和账户，最终回滚测试数据。
覆盖默认角色、跨部门账户授权、多部门主管、未分配保护、授权撤销及用户
停用。查询每次重新生成，不通过缓存保存允许账户集合，不访问真实业务数据。
"""

import os
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, select, text

from dashboard.access import visible_account_ids, visible_accounts
from dashboard.database import load_database_settings, make_engine
from dashboard.schema import accounts


def ids(connection: Connection, user: int) -> set[int]:
    """执行统一策略，取出当前可见账户 ID 便于断言。"""
    detailed = {int(row[0]) for row in connection.execute(visible_accounts(user))}
    # 实际放入单列 IN 子查询验证，避免仅检查选中列数量而遗漏 MySQL 1241。
    query = select(accounts.c.id).where(accounts.c.id.in_(visible_account_ids(user)))
    scalar: set[int] = set(connection.execute(query).scalars())
    assert scalar == detailed
    return scalar


@pytest.mark.parametrize("value", ["1junk", "1", 1.0, True, False, None, Decimal(1)])
def test_identity_rejects_implicit_conversion(value: object) -> None:
    """两个公开入口都在构造 SQL 前拒绝非原生整数，不输出输入内容。"""
    for query in (visible_account_ids, visible_accounts):
        with pytest.raises(TypeError) as error:
            query(cast(int, value))
        assert "1junk" not in str(error.value)


@pytest.mark.parametrize("value", [-(2**31) - 1, 2**31, 10**100])
def test_identity_rejects_integer_overflow(value: int) -> None:
    """范围必须与 access_users.id 的有符号 INT 一致，不能让数据库截断。"""
    for query in (visible_account_ids, visible_accounts):
        with pytest.raises(ValueError):
            query(value)


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要独立本机 MySQL"
)
def test_access_policy() -> None:
    """验证默认范围与配置变化，确保额外授权不能绕过未分配保护。"""
    settings = load_database_settings()
    assert settings.host == "127.0.0.1" and settings.name.startswith("tv2test")
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(settings)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                # 负数 ID 仅用于合成样本；若已有冲突则测试失败，不覆盖任何数据。
                conn.execute(
                    text(
                        "INSERT INTO access_departments VALUES (-1,'一部'),(-2,'二部')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO access_users VALUES (-1,'甲','bidder',-1,1),(-2,'乙','bidder',-2,1),(-3,'主管','manager',-1,1),(-4,'管理员','admin',NULL,1)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO access_accounts VALUES (-1,'甲账户',-1,-1),(-2,'乙账户',-2,-2),(-3,'未分配',NULL,-1)"
                    )
                )
                assert ids(conn, -1) == {-1}
                assert ids(conn, -2) == {-2}
                assert ids(conn, -3) == {-1}
                assert ids(conn, -4) == {-1, -2, -3}
                assert ids(conn, -999) == set()
                conn.execute(
                    text("INSERT INTO access_account_grants VALUES (-1,-2),(-1,-3)")
                )
                assert ids(conn, -1) == {-1, -2}
                conn.execute(text("DELETE FROM access_account_grants WHERE user_id=-1"))
                assert ids(conn, -1) == {-1}
                conn.execute(
                    text("INSERT INTO access_department_grants VALUES (-3,-2),(-1,-2)")
                )
                assert ids(conn, -3) == {-1, -2}
                assert ids(conn, -1) == {-1}
                conn.execute(text("UPDATE access_users SET role='bidder' WHERE id=-3"))
                assert ids(conn, -3) == set()
                conn.execute(
                    text(
                        "UPDATE access_accounts SET owner_id=-2,department_id=-2 WHERE id=-1"
                    )
                )
                assert ids(conn, -1) == set()
                assert ids(conn, -2) == {-1, -2}
                conn.execute(text("UPDATE access_users SET active=0 WHERE id=-4"))
                assert ids(conn, -4) == set()
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
