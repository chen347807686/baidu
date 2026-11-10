"""FastAPI 应用工厂与基础健康接口。

创建应用前校验统一配置，并注册具有明确响应模型的 /health 路由。
健康接口仅说明 HTTP 应用可响应，不代表数据库就绪或业务数据完整。
本模块不会自动建表、执行迁移或连接旧系统。
"""

from fastapi import FastAPI
from pydantic import BaseModel

from dashboard.config import load_settings


class Health(BaseModel):
    """健康接口响应结构，固定返回进程存活状态。"""

    status: str = "ok"


def create_app() -> FastAPI:
    """创建独立应用实例，便于命令行启动及测试隔离。"""
    load_settings()
    app = FastAPI(title="OneTitle Dashboard V2", version="0.1.0")

    @app.get("/health", response_model=Health)
    def health() -> Health:
        """返回基础存活状态；不在探针请求中执行数据库初始化。"""
        return Health()

    return app
