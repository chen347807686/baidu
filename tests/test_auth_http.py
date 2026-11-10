"""认证 HTTP 接口的真实 MySQL 集成测试。

提交合成账号后以应用数据库账号发起独立请求，验证 Cookie、来源防护、
参数脱敏、跨事务撤权/停用和多应用实例共享限速；结束时按依赖顺序清理样本。
测试不使用真实账户，也不输出密码或令牌。
"""

import hashlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from dashboard.app import create_app
from dashboard.auth import hash_password
from dashboard.auth_http import COOKIE
from dashboard.database import load_database_settings, make_engine


@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_APP_PASSWORD"), reason="需要独立本机 MySQL"
)
def test_browser_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    """以不同连接提交权限变化，后续请求必须立即读取新状态。"""
    settings = load_database_settings()
    assert settings.host == "127.0.0.1" and settings.name == "onetitle_v2_local"
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(settings)
    # 固定限速窗口，避免测试恰好跨分钟造成边界不确定。
    monkeypatch.setattr("dashboard.auth_http.time.time", lambda: 1800000000)
    bucket = hashlib.sha256(b"testclient:30000000").hexdigest()
    headers = {"Origin": "http://127.0.0.1:8082", "X-Onetitle-Request": "1"}
    body = {"username": "http-test", "password": "synthetic-http-password"}
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_users VALUES (-20,'HTTP样本','bidder',NULL,1)")
            )
            conn.execute(
                text("INSERT INTO access_accounts VALUES (-20,'HTTP账户',-20,NULL)")
            )
            conn.execute(
                text("INSERT INTO auth_credentials VALUES (-20,'http-test',:hash)"),
                {"hash": hash_password(body["password"])},
            )
            conn.execute(
                text("DELETE FROM auth_login_limits WHERE bucket=:bucket"),
                {"bucket": bucket},
            )
        monkeypatch.setenv("ONETITLE_ENVIRONMENT", "test")
        monkeypatch.setenv("ONETITLE_AUTH_ENABLED", "true")
        monkeypatch.setenv("ONETITLE_PORT", "8082")
        monkeypatch.setenv("ONETITLE_DB_USER", "v2_app")
        monkeypatch.setenv(
            "ONETITLE_DB_PASSWORD", os.environ["ONETITLE_TEST_APP_PASSWORD"]
        )
        with TestClient(create_app(), base_url="http://127.0.0.1:8082") as client:
            # 登录页与脚本同源提供，页面不缓存，脚本不使用内联事件。
            page = client.get("/")
            assert page.status_code == 200 and "登录工作台" in page.text
            assert "script-src 'self'" in page.headers["content-security-policy"]
            assert client.get("/assets/login.js").status_code == 200
            assert client.get("/assets/login.css").status_code == 200
            assert client.get("/api/v1/auth/me").status_code == 401
            assert client.get("/api/v1/reports/keywords").status_code == 401
            assert client.post("/api/v1/auth/login", json=body).status_code == 403
            assert (
                client.post(
                    "/api/v1/auth/login",
                    json=body,
                    headers={**headers, "Origin": "http://evil.example"},
                ).status_code
                == 403
            )
            invalid = client.post(
                "/api/v1/auth/login",
                json={**body, "password": "private-input" * 20},
                headers=headers,
            )
            assert invalid.status_code == 422 and "private-input" not in invalid.text
            result = client.post("/api/v1/auth/login", json=body, headers=headers)
            assert result.status_code == 200
            assert (
                client.get("/api/v1/reports/keywords").json()["data_source"]
                == "synthetic"
            )
            assert (
                client.get("/api/v1/reports/keywords?start=2026-11-09").status_code
                == 422
            )
            assert client.get("/api/v1/reports/keywords?page=0").status_code == 422
            assert client.get("/api/v1/reports/keywords?sort=unsafe").status_code == 422
            assert "HttpOnly" in result.headers["set-cookie"]
            assert "SameSite=strict" in result.headers["set-cookie"]
            assert result.headers["cache-control"] == "no-store"
            old_token = client.cookies.get(COOKIE)
            assert client.get("/api/v1/auth/me").json()["accounts"] == [
                {"id": -20, "name": "HTTP账户"}
            ]
            # 外部连接提交撤权，新请求不能沿用上个请求的事务快照。
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE access_accounts SET owner_id=NULL WHERE id=-20")
                )
            assert client.get("/api/v1/auth/me").json()["accounts"] == []
            assert (
                client.post("/api/v1/auth/logout", headers=headers).status_code == 204
            )
            assert client.cookies.get(COOKIE) is None
            assert (
                client.get(
                    "/api/v1/auth/me", headers={"Cookie": f"{COOKIE}={old_token}"}
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/v1/auth/login", json=body, headers=headers
                ).status_code
                == 200
            )
            with engine.begin() as conn:
                conn.execute(text("UPDATE access_users SET active=0 WHERE id=-20"))
            assert client.get("/api/v1/auth/me").status_code == 401
            # 已有两次登录，八次失败仍返回 401；第十一次开始返回 429。
            for _ in range(8):
                assert (
                    client.post(
                        "/api/v1/auth/login",
                        json={**body, "password": "wrong"},
                        headers=headers,
                    ).status_code
                    == 401
                )
        with TestClient(create_app(), base_url="http://127.0.0.1:8082") as second:
            limited = second.post("/api/v1/auth/login", json=body, headers=headers)
            assert limited.status_code == 429 and "Retry-After" in limited.headers
    finally:
        # 只删除本测试固定 ID，避免清理其它开发数据。
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM auth_sessions WHERE user_id=-20"))
            conn.execute(text("DELETE FROM auth_credentials WHERE user_id=-20"))
            conn.execute(text("DELETE FROM access_accounts WHERE id=-20"))
            conn.execute(text("DELETE FROM access_users WHERE id=-20"))
            conn.execute(
                text("DELETE FROM auth_login_limits WHERE bucket=:bucket"),
                {"bucket": bucket},
            )
        engine.dispose()
