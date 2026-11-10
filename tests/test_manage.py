"""本机账号维护工具的真实数据库验证。

在专用本地库事务内初始化合成管理员，验证重复初始化拒绝、重置后全部
会话撤销及操作记录不含秘密，最后回滚所有样本。公共夹具创建独立空库，
已有本地管理员或其它测试进程不会影响首次初始化的前置条件。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard.auth import AuthenticationError, authenticate, login
from dashboard.database import load_database_settings, make_engine
from dashboard.manage import update_account


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要独立本机 MySQL"
)
def test_account_maintenance() -> None:
    """验证初始化与重置的一致性，维护命令失败不能悄悄创建第二个管理员。"""
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
                user = update_account(
                    conn,
                    "bootstrap",
                    "maintenance-test",
                    "first-synthetic-password",
                    "test-operator",
                )
                assert (
                    conn.execute(
                        text("SELECT role FROM access_users WHERE id=:id"), {"id": user}
                    ).scalar()
                    == "admin"
                )
                with pytest.raises(ValueError, match="已有登录账号"):
                    update_account(
                        conn,
                        "bootstrap",
                        "other",
                        "other-synthetic-password",
                        "test-operator",
                    )
                tokens = [
                    login(conn, "maintenance-test", "first-synthetic-password")
                    for _ in range(2)
                ]
                assert all(authenticate(conn, token) == user for token in tokens)
                update_account(
                    conn,
                    "reset",
                    "maintenance-test",
                    "second-synthetic-password",
                    "test-operator",
                )
                for token in tokens:
                    with pytest.raises(AuthenticationError):
                        authenticate(conn, token)
                with pytest.raises(AuthenticationError):
                    login(conn, "maintenance-test", "first-synthetic-password")
                assert (
                    authenticate(
                        conn,
                        login(conn, "maintenance-test", "second-synthetic-password"),
                    )
                    == user
                )
                events = conn.execute(
                    text(
                        "SELECT action,operator FROM auth_account_events WHERE user_id=:id ORDER BY id"
                    ),
                    {"id": user},
                ).all()
                assert events == [
                    ("bootstrap", "test-operator"),
                    ("reset", "test-operator"),
                ]
                with pytest.raises(ValueError, match="不存在"):
                    update_account(
                        conn,
                        "reset",
                        "missing-maintenance-user",
                        "second-synthetic-password",
                        "test-operator",
                    )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
