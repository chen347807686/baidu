"""FastAPI 应用工厂与基础健康接口。

创建应用前校验统一配置，并注册具有明确响应模型的 /health 路由。
健康接口仅说明 HTTP 应用可响应，不代表数据库就绪或业务数据完整。
显式开启认证后注册数据库登录接口，使用每请求事务；应用退出释放连接池。
本模块不会自动建表、执行迁移或连接旧系统。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard.auth_http import register_auth
from dashboard.config import load_settings
from dashboard.database import load_database_settings, make_engine


class Health(BaseModel):
    """健康接口响应结构，固定返回进程存活状态。"""

    status: str = "ok"


def create_app() -> FastAPI:
    """创建独立应用实例，便于命令行启动及测试隔离。"""
    settings = load_settings()
    engine = make_engine(load_database_settings()) if settings.auth_enabled else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """应用停止时释放数据库连接池，启动不执行任何迁移。"""
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title="OneTitle Dashboard V2", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """验证失败只返回通用提示，避免默认错误响应回显密码输入。"""
        return JSONResponse(
            status_code=422,
            content={"detail": "请求参数无效"},
            headers={"Cache-Control": "no-store"},
        )

    if engine is not None:
        register_auth(app, engine, settings.port)
        # 本步同源静态登录页用于本地验证，核心业务前端仍按独立批次推进。
        web = Path(__file__).parent / "web"
        app.mount("/assets", StaticFiles(directory=web), name="login-assets")

        @app.get("/", include_in_schema=False)
        def login_page() -> FileResponse:
            """返回登录页，仅允许同源脚本和样式，不缓存页面内容。"""
            return FileResponse(
                web / "index.html",
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )

    @app.get("/health", response_model=Health)
    def health() -> Health:
        """返回基础存活状态；不在探针请求中执行数据库初始化。"""
        return Health()

    return app
