"""Explicit database setup; engine creation never runs migrations."""

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, Engine, create_engine

from dashboard.config import ConfigurationError


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ONETITLE_DB_", extra="ignore")
    host: str = "127.0.0.1"
    port: int = Field(default=13306, ge=1, le=65535)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
    user: str = Field(min_length=1)
    password: SecretStr


def load_database_settings() -> DatabaseSettings:
    try:
        return DatabaseSettings()
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError(
            "Invalid database fields: " + ", ".join(fields)
        ) from None


def make_engine(settings: DatabaseSettings) -> Engine:
    url = URL.create(
        "mysql+pymysql",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)
