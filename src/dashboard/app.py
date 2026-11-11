"""FastAPI 应用工厂与基础健康接口。

创建应用前校验统一配置，并注册具有明确响应模型的 /health 路由。
健康接口仅说明 HTTP 应用可响应，不代表数据库就绪或业务数据完整。
显式开启认证后注册数据库登录接口，使用每请求事务；应用退出释放连接池。
本模块不会自动建表、执行迁移或连接旧系统。
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard.auth_http import register_auth
from dashboard.config import Settings, load_settings
from dashboard.daily_reports import register_daily_reports
from dashboard.database import load_database_settings, make_engine
from dashboard.keyword_actions import register_keyword_actions
from dashboard.reports import register_reports


class Health(BaseModel):
    """健康接口响应结构，固定返回进程存活状态。"""

    status: str = "ok"


def create_app(
    settings: Settings | None = None, *, clock: Callable[[], float] = time
) -> FastAPI:
    """使用已校验配置创建应用；独立调用时读取配置，限速时钟可按实例注入。

    clock 仅用于登录限速窗口，不改变会话有效期或密码处理。默认使用系统
    墙上时钟；测试可传入固定时钟，不修改 Python 全局时间函数。
    """
    if settings is None:
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

    web = Path(__file__).parent / "web"
    # 页面和静态资源即使未开启数据库认证也可访问，便于本地预览登录界面。
    # 业务 API 仍只在认证与数据库启用时注册，避免预览模式误读真实数据。
    app.mount("/assets", StaticFiles(directory=web), name="login-assets")

    @app.get("/", include_in_schema=False)
    def login_page() -> FileResponse:
        """返回本地页面；未开启认证时页面会显示服务配置提示。"""
        return FileResponse(
            web / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    if engine is not None:
        register_auth(app, engine, settings.port, clock=clock)
        register_reports(app, engine)
        register_daily_reports(app, engine)
        register_keyword_actions(app, engine)

    @app.get("/health", response_model=Health)
    def health() -> Health:
        """返回基础存活状态；不在探针请求中执行数据库初始化。"""
        return Health()

    return app
