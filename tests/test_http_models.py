"""HTTP 响应模型与 OpenAPI 的纯契约测试。

不连接数据库，验证金额字符串、零分母 null、未知转化的来源差异、额外字段
拒绝和接口文档引用。错误契约不能被静默强制转换为看似正常的成功响应。
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dashboard.app import create_app
from dashboard.http_models import CurrentUserResponse, KeywordReportResponse
from dashboard.reports import metrics


def result(source: str = "synthetic") -> dict[str, Any]:
    """构造与服务输出一致的零数据结果，不通过真实文件或账号取值。"""
    summary = metrics({"conv_78": 0, "conv_92": 0} if source == "formal" else {})
    return {
        "rows": [],
        "total": 0,
        "summary": summary,
        "page": 1,
        "page_size": 50,
        "data_source": source,
        "applied_filters": {
            "start": "2026-11-03",
            "end": "2026-11-09",
            "account": None,
            "search": "",
            "sort": "cost",
            "order": "desc",
            "source": source,
        },
    }


def test_decimal_strings_null_and_optional_fields() -> None:
    """省略不存在的转化列，但不能把明确表示零分母的 null 也省略。"""
    data = KeywordReportResponse.model_validate(result()).model_dump(
        mode="json", exclude_unset=True
    )
    assert data["summary"]["cost"] == "0.0000" and "conv_78" not in data["summary"]
    assert "lead_cost" in data["summary"] and data["summary"]["lead_cost"] is None
    assert data["applied_filters"]["account"] is None
    formal = KeywordReportResponse.model_validate(result("formal")).model_dump(
        mode="json", exclude_unset=True
    )
    assert formal["summary"]["conv_78"] == 0 and formal["summary"]["conv_92"] == 0


@pytest.mark.parametrize("bad_cost", [1.23, "1.23", None])
def test_invalid_metric_contract_is_rejected(bad_cost: object) -> None:
    """金额不得被响应层悄悄转为浮点或丢失约定小数位。"""
    data = result()
    data["summary"]["cost"] = bad_cost
    with pytest.raises(ValidationError):
        KeywordReportResponse.model_validate(data)


def test_source_consistency() -> None:
    """正式来源不能缺原字段，合成来源不能伪造，来源标签必须一致。"""
    data = result()
    data["data_source"] = "formal"
    with pytest.raises(ValidationError):
        KeywordReportResponse.model_validate(data)
    data = result("formal")
    del data["summary"]["conv_92"]
    with pytest.raises(ValidationError):
        KeywordReportResponse.model_validate(data)
    data = result()
    data["summary"]["conv_78"] = 0
    with pytest.raises(ValidationError):
        KeywordReportResponse.model_validate(data)


def test_identity_response_does_not_allow_secrets() -> None:
    """当前用户契约只含身份和账户，额外秘密字段即使误传也不能成功返回。"""
    with pytest.raises(ValidationError) as caught:
        CurrentUserResponse.model_validate(
            {"user_id": 1, "accounts": [], "password": "synthetic-private-value"}
        )
    assert "synthetic-private-value" not in str(caught.value)
    with pytest.raises(ValidationError):
        CurrentUserResponse.model_validate({"user_id": True, "accounts": []})


def test_openapi_declares_concrete_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    """应用文档显式引用模型，客户端能获知字段和金额类型，无数据库连接。"""
    for name, value in {
        "ONETITLE_ENVIRONMENT": "test",
        "ONETITLE_AUTH_ENABLED": "true",
        "ONETITLE_DB_NAME": "unused",
        "ONETITLE_DB_USER": "synthetic",
        "ONETITLE_DB_PASSWORD": "synthetic",
    }.items():
        monkeypatch.setenv(name, value)
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()
    for path, method, model in [
        ("/api/v1/auth/login", "post", "LoginResponse"),
        ("/api/v1/auth/me", "get", "CurrentUserResponse"),
        ("/api/v1/reports/keywords", "get", "KeywordReportResponse"),
    ]:
        assert (
            schema["paths"][path][method]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["$ref"]
            == f"#/components/schemas/{model}"
        )
    definitions = schema["components"]["schemas"]
    assert definitions["KeywordMetrics"]["properties"]["cost"]["type"] == "string"
    assert "accounts" in definitions["CurrentUserResponse"]["required"]
