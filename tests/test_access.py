"""在真实本机 MySQL 中验证可配置账户权限。

测试迁移完成后在事务中写入合成人员、部门和账户，最终回滚测试数据。
覆盖默认角色、跨部门账户授权、多部门主管、未分配保护、授权撤销及用户
停用。查询每次重新生成，不通过缓存保存允许账户集合，不访问真实业务数据。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from dashboard.access import visible_accounts
from dashboard.database import load_database_settings, make_engine


def ids(connection: Connection, user: int) -> set[int]:
    """执行统一策略，取出当前可见账户 ID 便于断言。"""
    return {int(row[0]) for row in connection.execute(visible_accounts(user))}


@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_APP_PASSWORD"), reason="需要独立本机 MySQL"
)
def test_access_policy() -> None:
    """验证默认范围与配置变化，确保额外授权不能绕过未分配保护。"""
    settings = load_database_settings()
    assert settings.host == "127.0.0.1" and settings.name == "onetitle_v2_local"
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
