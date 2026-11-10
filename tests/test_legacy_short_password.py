"""历史短密码的 HTTP 登录回归，不放宽新密码或重置策略。

隔离库中独立生成旧格式短密码，验证错误密码不更新、正确密码登录与升级
均成功，升级后仍可再次登录；实际本地管理员不参与测试。
"""

import hashlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from dashboard import auth
from dashboard.app import create_app
from dashboard.database import load_database_settings, make_engine
from dashboard.manage import update_account


@pytest.mark.mysql
@pytest.mark.parametrize("rounds", [100000, 599999])
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_short_legacy_login_upgrades_without_500(
    monkeypatch: pytest.MonkeyPatch, rounds: int
) -> None:
    """历史六位密码不因升级参数触发最小长度错误，新建和重置仍拒绝。"""
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(load_database_settings())
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac("sha256", b"old123", salt, rounds)
    old = f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO access_users(name,role,active) VALUES ('短密码样本','bidder',1)"
                )
            )
            user = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            conn.execute(
                text(
                    "INSERT INTO auth_credentials(user_id,login,password_hash) VALUES (:id,'legacy-short',:hash)"
                ),
                {"id": user, "hash": old},
            )
        for name, value in {
            "ONETITLE_ENVIRONMENT": "test",
            "ONETITLE_AUTH_ENABLED": "true",
            "ONETITLE_PORT": "8082",
            "ONETITLE_DB_USER": os.environ["ONETITLE_TEST_APP_USER"],
            "ONETITLE_DB_PASSWORD": os.environ["ONETITLE_TEST_APP_PASSWORD"],
        }.items():
            monkeypatch.setenv(name, value)
        headers = {"Origin": "http://127.0.0.1:8082", "X-Onetitle-Request": "1"}
        with TestClient(create_app(), base_url="http://127.0.0.1:8082") as client:
            assert (
                client.post(
                    "/api/v1/auth/login",
                    headers=headers,
                    json={"username": "legacy-short", "password": "wrong"},
                ).status_code
                == 401
            )
            with engine.connect() as conn:
                assert (
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=:id"
                        ),
                        {"id": user},
                    ).scalar_one()
                    == old
                )
            response = client.post(
                "/api/v1/auth/login",
                headers=headers,
                json={"username": "legacy-short", "password": "old123"},
            )
            assert response.status_code == 200 and response.json() == {"status": "ok"}
            assert client.get("/api/v1/auth/me").json()["user_id"] == user
            with engine.connect() as conn:
                upgraded = str(
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=:id"
                        ),
                        {"id": user},
                    ).scalar_one()
                )
                assert upgraded != old and upgraded.startswith(
                    f"pbkdf2_sha256${auth.ITERATIONS}$"
                )
            assert (
                client.post(
                    "/api/v1/auth/login",
                    headers=headers,
                    json={"username": "legacy-short", "password": "old123"},
                ).status_code
                == 200
            )
        with pytest.raises(ValueError):
            auth.hash_password("old123")
        with engine.begin() as conn:
            with pytest.raises(ValueError):
                update_account(conn, "reset", "legacy-short", "old123", "test")
            assert (
                conn.execute(
                    text(
                        "SELECT password_hash FROM auth_credentials WHERE user_id=:id"
                    ),
                    {"id": user},
                ).scalar_one()
                == upgraded
            )
    finally:
        engine.dispose()
