"""密码与服务端会话认证测试。

纯函数测试覆盖随机盐和损坏记录；真实 MySQL 测试写入合成用户，在事务中
验证成功登录、错误凭据、伪造令牌、退出、过期及停用，最后回滚合成数据。
不创建可供业务使用的默认账号或密码，不把任何令牌写入输出。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard.auth import (
    AuthenticationError,
    authenticate,
    hash_password,
    login,
    logout,
    verify_password,
)
from dashboard.database import load_database_settings, make_engine


def test_password_hash() -> None:
    """相同密码使用不同随机盐，错误密码和损坏哈希均失败。"""
    password = "synthetic-test-password"
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert verify_password(password, first)
    assert not verify_password("wrong", first)
    assert not verify_password(password, "broken")
    with pytest.raises(ValueError):
        hash_password("short")


@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_APP_PASSWORD"), reason="需要独立本机 MySQL"
)
def test_persistent_authentication() -> None:
    """验证会话生命周期与用户状态，所有合成数据最终回滚。"""
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
                conn.execute(
                    text(
                        "INSERT INTO access_users VALUES (-10,'认证样本','bidder',NULL,1)"
                    )
                )
                conn.execute(
                    text("INSERT INTO auth_credentials VALUES (-10,'auth-test',:hash)"),
                    {"hash": hash_password("synthetic-test-password")},
                )
                for username, password in [
                    ("missing", "synthetic-test-password"),
                    ("auth-test", "wrong"),
                ]:
                    with pytest.raises(AuthenticationError, match="登录失败"):
                        login(conn, username, password)
                token = login(conn, "auth-test", "synthetic-test-password")
                assert authenticate(conn, token) == -10
                assert (
                    conn.execute(
                        text("SELECT token_hash FROM auth_sessions WHERE user_id=-10")
                    ).scalar()
                    != token
                )
                with pytest.raises(AuthenticationError):
                    authenticate(conn, "forged")
                logout(conn, token)
                logout(conn, token)
                with pytest.raises(AuthenticationError):
                    authenticate(conn, token)
                token = login(conn, "auth-test", "synthetic-test-password")
                conn.execute(
                    text(
                        "UPDATE auth_sessions SET expires_at='2000-01-01' WHERE user_id=-10"
                    )
                )
                with pytest.raises(AuthenticationError):
                    authenticate(conn, token)
                token = login(conn, "auth-test", "synthetic-test-password")
                conn.execute(text("UPDATE access_users SET active=0 WHERE id=-10"))
                with pytest.raises(AuthenticationError):
                    authenticate(conn, token)
                with pytest.raises(AuthenticationError):
                    login(conn, "auth-test", "synthetic-test-password")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
