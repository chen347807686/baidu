import pytest
from fastapi.testclient import TestClient

from dashboard.__main__ import main
from dashboard.app import create_app
from dashboard.config import ConfigurationError, load_settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENVIRONMENT", "HOST", "PORT"):
        monkeypatch.delenv("ONETITLE_" + name, raising=False)


def test_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONETITLE_ENVIRONMENT", "test")
    with TestClient(create_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_required_configuration() -> None:
    with pytest.raises(ConfigurationError, match="environment"):
        create_app()


@pytest.mark.parametrize("value", ["0", "1023", "65536", "sensitive-invalid-value"])
def test_invalid_port_is_redacted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ONETITLE_ENVIRONMENT", "test")
    monkeypatch.setenv("ONETITLE_PORT", value)
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert str(error.value) == "Invalid configuration fields: port"


def test_cli_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 2
    assert capsys.readouterr().err == "Invalid configuration fields: environment\n"
