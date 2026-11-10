"""MySQL 配置与 SQLAlchemy 连接引擎入口。

从 ONETITLE_DB_ 环境变量读取连接参数，密码使用 SecretStr 避免普通展示泄露。
引擎只负责连接池管理，不建表、不迁移、不插入默认账户；结构变更由 Alembic
显式执行。应用与迁移可传入不同账号，以数据库授权限制各自操作范围。
"""

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, Engine, create_engine

from dashboard.config import ConfigurationError


class DatabaseSettings(BaseSettings):
    """数据库连接参数；库名限制字符范围，账号与密码必须显式提供。"""

    model_config = SettingsConfigDict(env_prefix="ONETITLE_DB_", extra="ignore")
    host: str = "127.0.0.1"
    port: int = Field(default=13306, ge=1, le=65535)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
    user: str = Field(min_length=1)
    password: SecretStr


def load_database_settings() -> DatabaseSettings:
    """校验数据库参数，并将失败转换为不含输入值的配置异常。"""
    try:
        return DatabaseSettings()
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError(
            "Invalid database fields: " + ", ".join(fields)
        ) from None


def make_engine(settings: DatabaseSettings) -> Engine:
    """创建延迟连接的引擎，调用方使用完毕后负责 dispose 释放连接池。"""
    # 使用 URL 对象处理密码中的特殊字符，避免手工拼接连接串。
    url = URL.create(
        "mysql+pymysql",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
        query={"charset": "utf8mb4"},
    )
    # 取出连接前探测存活；隐藏 SQL 参数，但这不能代替调用方的日志脱敏。
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)
