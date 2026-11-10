"""本机维护配置的纯校验及连接构造测试，不连接真实数据库。

通过临时合成凭据验证默认兼容、应用环境不能改变维护目标、SecretStr 展示
脱敏、非法文件和配置安全拒绝。管理员命令另验证配置失败不会询问新密码。
"""

import json
import sys
from pathlib import Path

import pytest

from dashboard import manage
from dashboard.config import ConfigurationError
from dashboard.database import DatabaseSettings, make_engine
from dashboard.local_config import (
    load_local_credentials,
    load_local_mysql_settings,
    load_maintenance_database_settings,
    require_local_database,
)


@pytest.fixture(autouse=True)
def local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除本机设置覆盖，避免开发者个人环境影响纯配置测试。"""
    for name in ("HOST", "PORT", "DATABASE", "CREDENTIALS_PATH"):
        monkeypatch.delenv("ONETITLE_LOCAL_MYSQL_" + name, raising=False)


def credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None = None
) -> Path:
    """创建合成凭据文件并显式选择它，不读取真实运行秘密。"""
    path = tmp_path / "local.json"
    path.write_text(
        content
        if content is not None
        else json.dumps(
            {
                "root": "synthetic-root-secret",
                "migrator": "synthetic-migration-secret",
                "app": "synthetic-app-secret",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONETITLE_LOCAL_MYSQL_CREDENTIALS_PATH", str(path))
    return path


def test_maintenance_uses_shared_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """维护配置不受应用环境影响，端口集中修改后边界校验同步生效。"""
    credentials(tmp_path, monkeypatch)
    monkeypatch.setenv("ONETITLE_DB_HOST", "unrelated.example")
    monkeypatch.setenv("ONETITLE_DB_PORT", "invalid-input")
    monkeypatch.setenv("ONETITLE_DB_USER", "unrelated-user")
    monkeypatch.setenv("ONETITLE_LOCAL_MYSQL_PORT", "13307")
    settings = load_maintenance_database_settings()
    assert (
        settings.host == "127.0.0.1"
        and settings.port == 13307
        and settings.user == "root"
    )
    assert settings.name == "onetitle_v2_local"
    assert "synthetic-root-secret" not in repr(settings)
    require_local_database(settings)
    engine = make_engine(settings)
    try:
        assert engine.url.port == 13307 and engine.hide_parameters
    finally:
        engine.dispose()
    other = DatabaseSettings(
        host="remote.example",
        port=13307,
        name="onetitle_v2_local",
        user="test",
        password="synthetic",
    )
    with pytest.raises(ConfigurationError):
        require_local_database(other)


@pytest.mark.parametrize(
    "content",
    [
        '{"root":"private-value",',
        '{"root":"private-value","root":"other"}',
        '{"root":"","migrator":"test","app":"test"}',
        '{"root":true,"migrator":"test","app":"test"}',
        '{"root":"private-value","migrator":"test","app":"test","extra":"secret"}',
    ],
)
def test_credentials_fail_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """损坏 JSON、重复键、空秘密、错误类型和未知键不能回显秘密。"""
    credentials(tmp_path, monkeypatch, content)
    with pytest.raises(ConfigurationError) as caught:
        load_local_credentials(load_local_mysql_settings())
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("HOST", "remote.example"),
        ("PORT", "0"),
        ("DATABASE", "other_database"),
        ("CREDENTIALS_PATH", "relative.json"),
    ],
)
def test_invalid_local_settings_are_redacted(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    """只允许本机目标，非法设置仅返回字段名。"""
    monkeypatch.setenv("ONETITLE_LOCAL_MYSQL_" + key, value)
    with pytest.raises(ConfigurationError) as caught:
        load_local_mysql_settings()
    assert value not in str(caught.value)


def test_manage_configuration_failure_does_not_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """配置失败应在询问新密码之前退出，且不尝试数据库连接。"""
    credentials(tmp_path, monkeypatch, "broken-private-value")
    monkeypatch.setattr(sys, "argv", ["manage", "bootstrap", "--username", "synthetic"])

    def unexpected_prompt(prompt: str) -> str:
        """若配置失败后仍提示密码则立即使测试失败。"""
        raise AssertionError("不应询问密码")

    monkeypatch.setattr("dashboard.manage.getpass.getpass", unexpected_prompt)
    assert manage.main() == 2
    assert "private-value" not in capsys.readouterr().out
