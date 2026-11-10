"""本地服务与非敏感配置的自动化测试。

覆盖健康响应、缺失必填配置、非法端口脱敏及命令行错误退出。
每项测试隔离相关环境变量，不连接真实数据库或外部服务。
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.__main__ import main
from dashboard.app import create_app
from dashboard.config import ConfigurationError, load_settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除会影响测试的运行配置，pytest 会在测试后恢复原环境。"""
    for name in ("ENVIRONMENT", "HOST", "PORT"):
        monkeypatch.delenv("ONETITLE_" + name, raising=False)


def test_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """确认健康接口的状态码与响应体满足契约。"""
    monkeypatch.setenv("ONETITLE_ENVIRONMENT", "test")
    with TestClient(create_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_required_configuration() -> None:
    """缺少运行环境时应拒绝创建应用，而不是静默选择默认环境。"""
    with pytest.raises(ConfigurationError, match="environment"):
        create_app()


@pytest.mark.parametrize("value", ["0", "1023", "65536", "sensitive-invalid-value"])
def test_invalid_port_is_redacted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """覆盖端口上下界与非数字输入，确保报错不回显输入值。"""
    monkeypatch.setenv("ONETITLE_ENVIRONMENT", "test")
    monkeypatch.setenv("ONETITLE_PORT", value)
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert str(error.value) == "Invalid configuration fields: port"


def test_cli_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """确认启动失败使用约定退出码，并且 stderr 只含安全提示。"""
    assert main() == 2
    assert capsys.readouterr().err == "Invalid configuration fields: environment\n"
