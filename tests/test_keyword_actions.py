"""关键词预览路由的响应、认证和权限回归。

使用隔离的模拟连接验证 HTTP 链路及实际响应序列化，不代替 MySQL 集成验收，
不访问运行库或模型服务，也不生成真实业务状态。
"""

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth import AuthenticationError
from dashboard.keyword_actions import register_keyword_actions


@pytest.fixture
def preview_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, MagicMock]]:
    """注册真实路由，只替换数据库连接和会话认证。"""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.first.return_value = (7,)
    monkeypatch.setattr("dashboard.keyword_actions.authenticate", lambda conn, token: 1)
    app = FastAPI()
    register_keyword_actions(app, engine)
    with TestClient(app) as client:
        yield client, conn


def expansion_body() -> dict[str, object]:
    """显式传入模拟种子状态，不冒充真实持久种子。"""
    return {"account_id": 7, "seed_id": "test-1", "keyword": "黄金回收", "quality": "high", "negative_status": "clear"}


def test_expansion_http_serialization(preview_client: tuple[TestClient, MagicMock]) -> None:
    """确认 dataclass 候选能转换为响应且保留原词与模拟标识。"""
    client, _ = preview_client
    response = client.post("/api/v1/keywords/expansion-preview", json=expansion_body())
    assert response.status_code == 200
    body = response.json()
    assert body["persistent"] is False and body["mode"] == "simulation"
    assert len(body["candidates"]) == 2
    assert all(row["source_keyword"] == "黄金回收" and row["account_id"] == 7 for row in body["candidates"])
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status", ["unknown", "suggested", "confirmed"])
def test_expansion_rejects_negative_seed(preview_client: tuple[TestClient, MagicMock], status: str) -> None:
    """否词未通过的种子不能进入模拟生成器。"""
    client, _ = preview_client
    assert client.post("/api/v1/keywords/expansion-preview", json={**expansion_body(), "negative_status": status}).status_code == 422


def test_expansion_denies_hidden_account(preview_client: tuple[TestClient, MagicMock]) -> None:
    """无权账户不得返回候选。"""
    client, conn = preview_client
    conn.execute.return_value.first.return_value = None
    assert client.post("/api/v1/keywords/expansion-preview", json=expansion_body()).status_code == 403


def test_expired_session(preview_client: tuple[TestClient, MagicMock], monkeypatch: pytest.MonkeyPatch) -> None:
    """过期会话不能读取或生成预览结果。"""
    def reject(conn: object, token: str) -> int:
        """模拟会话已失效。"""
        raise AuthenticationError("会话无效")

    monkeypatch.setattr("dashboard.keyword_actions.authenticate", reject)
    client, _ = preview_client
    assert client.post("/api/v1/keywords/expansion-preview", json=expansion_body()).status_code == 401


def test_negative_preview_http(preview_client: tuple[TestClient, MagicMock]) -> None:
    """否词预览只返回命中证据，不输出相关性分类。"""
    client, _ = preview_client
    response = client.post("/api/v1/keywords/negative-preview", json={"account_id": 7, "word": "黄金招聘", "custom_negatives": ["招聘"]})
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True and body["persistent"] is False
    assert "招聘" in body["reason"] and "relevance" not in body
