"""应用运行配置的统一读取与校验入口。

从 ONETITLE_ 前缀的环境变量读取环境、监听地址和端口，不自动加载 .env。
目前只允许本地或测试环境；非法配置转换为仅含字段名的安全异常，
避免校验器的原始输入值进入终端、日志或调用方的错误信息。
"""

from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    """可安全展示的配置错误，错误内容不得包含原始配置值。"""


class Settings(BaseSettings):
    """本地服务配置：环境必填，地址限制为回环地址，端口有边界校验。"""

    model_config = SettingsConfigDict(env_prefix="ONETITLE_", extra="ignore")

    environment: Literal["local", "test"]
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8082, ge=1024, le=65535)


def load_settings() -> Settings:
    """读取并验证环境变量，失败时只报告需要修正的字段。"""
    try:
        return Settings()
    except ValidationError as exc:
        # 不直接打印 ValidationError，它可能携带用户输入的敏感内容。
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError(
            "Invalid configuration fields: " + ", ".join(fields)
        ) from None
