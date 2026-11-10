"""本机 MySQL 工具与维护命令的统一配置入口。

非敏感设置从 ONETITLE_LOCAL_MYSQL_ 环境变量读取；秘密只读取显式受限文件，
以 SecretStr 保存。维护连接使用明确的本地配置，不继承 ONETITLE_DB_* 指向。
本模块只读取和校验，不创建文件、连接、数据库或账号；连接统一交给 make_engine。
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from dashboard.config import ConfigurationError
from dashboard.database import DatabaseSettings


class LocalMySQLSettings(BaseSettings):
    """本机工具设置：只允许回环地址和独立运行库，端口与文件路径集中管理。"""

    model_config = SettingsConfigDict(
        env_prefix="ONETITLE_LOCAL_MYSQL_", extra="ignore"
    )
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=13306, ge=1024, le=65535)
    database: Literal["onetitle_v2_local"] = "onetitle_v2_local"
    credentials_path: Path = (
        Path(__file__).resolve().parents[2] / ".runtime/mysql/local.json"
    )

    @field_validator("credentials_path")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        """路径必须明确，不随命令所在目录意外切换凭据文件。"""
        if not value.is_absolute():
            raise ValueError("必须提供绝对路径")
        return value


class LocalCredentials(BaseModel):
    """当前本机实例的三个受限密码，不允许空值或未知字段。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    root: SecretStr = Field(min_length=1)
    migrator: SecretStr = Field(min_length=1)
    app: SecretStr = Field(min_length=1)


def load_local_mysql_settings() -> LocalMySQLSettings:
    """统一校验非敏感配置，失败只报告字段，不输出输入值。"""
    try:
        return LocalMySQLSettings()
    except ValidationError as exc:
        fields = ", ".join(sorted({str(error["loc"][0]) for error in exc.errors()}))
        raise ConfigurationError("本机 MySQL 配置字段无效：" + fields) from None


def load_local_credentials(settings: LocalMySQLSettings) -> LocalCredentials:
    """限量读取受限 JSON；所有文件、解析或模型错误均转成安全提示。"""

    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """重复键拒绝，不能静默选择另一个凭据值。"""
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复字段")
            result[key] = value
        return result

    try:
        with settings.credentials_path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("文件过大")
        values = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
        return LocalCredentials.model_validate(values)
    except (OSError, ValueError, TypeError, RecursionError):
        raise ConfigurationError(
            "本机 MySQL 凭据文件不可用，请检查文件与权限；未输出内容"
        ) from None


def load_maintenance_database_settings() -> DatabaseSettings:
    """将本机维护凭据转为统一数据库配置；显式字段覆盖应用连接环境变量。"""
    local = load_local_mysql_settings()
    credentials = load_local_credentials(local)
    return DatabaseSettings(
        host=local.host,
        port=local.port,
        name=local.database,
        user="root",
        password=credentials.root,
    )


def require_local_database(settings: DatabaseSettings) -> None:
    """本机导入命令共用目标边界校验，不自行复制地址、端口和库名。"""
    local = load_local_mysql_settings()
    if (settings.host, settings.port, settings.name) != (
        local.host,
        local.port,
        local.database,
    ):
        raise ConfigurationError("仅允许当前本机 V2 数据库")
