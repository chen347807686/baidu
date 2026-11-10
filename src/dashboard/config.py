"""Single configuration boundary; never expose validation input."""

from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    """Safe startup error without configuration values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ONETITLE_", extra="ignore")

    environment: Literal["local", "test"]
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8082, ge=1024, le=65535)


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError(
            "Invalid configuration fields: " + ", ".join(fields)
        ) from None
