"""浏览器认证接口、同源防护与数据库登录限速。

注册登录、退出和当前用户接口。状态修改必须带匹配本地端口的 Origin 和
自定义请求头，且不开放 CORS；会话放入独立命名的 HttpOnly/SameSite Cookie。
每个请求使用新事务，避免旧快照延迟停用或撤权。仅支持本机 HTTP 开发，
上线 HTTPS 时需启用 Secure Cookie，并单独配置代理来源及可信站点。
"""

import hashlib
import time

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import Engine, text
from starlette.middleware.base import RequestResponseEndpoint

from dashboard.access import visible_accounts
from dashboard.auth import AuthenticationError, authenticate, login, logout
from dashboard.auth_constants import COOKIE
from dashboard.http_models import CurrentUserResponse, LoginResponse, VisibleAccount


class LoginInput(BaseModel):
    """登录输入限制长度，密码在对象展示时脱敏。"""

    username: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=128)


def consume_attempt(engine: Engine, source: str) -> None:
    """每来源每分钟最多十次尝试；独立提交，错误密码不能回滚限速计数。"""
    window = int(time.time()) // 60
    bucket = hashlib.sha256(f"{source}:{window}".encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM auth_login_limits WHERE `window` < :old"),
            {"old": window - 1},
        )
        conn.execute(
            text(
                "INSERT INTO auth_login_limits (bucket,`window`,attempts) VALUES (:bucket,:window,1) "
                "ON DUPLICATE KEY UPDATE attempts=attempts+1"
            ),
            {"bucket": bucket, "window": window},
        )
        attempts: int = conn.execute(
            text(
                "SELECT attempts FROM auth_login_limits WHERE bucket=:bucket FOR UPDATE"
            ),
            {"bucket": bucket},
        ).scalar_one()
    if attempts > 10:
        raise HTTPException(
            429,
            "登录尝试过多，请稍后重试",
            headers={"Retry-After": str(60 - int(time.time()) % 60)},
        )


def register_auth(app: FastAPI, engine: Engine, port: int) -> None:
    """注册本地认证路由，数据库连接由每次请求分别管理。"""
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    @app.middleware("http")
    async def prevent_auth_cache(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """认证成功及失败响应都禁止缓存，避免浏览器复用旧身份信息。"""
        response = await call_next(request)
        if request.url.path.startswith(("/api/v1/auth/", "/api/v1/reports/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_same_origin(request: Request) -> None:
        """拒绝缺失或伪造站点来源的浏览器跨站请求，不信任转发地址头。"""
        if (
            request.headers.get("origin") not in origins
            or request.headers.get("x-onetitle-request") != "1"
        ):
            raise HTTPException(403, "请求来源无效")

    @app.post("/api/v1/auth/login", response_model=LoginResponse)
    def sign_in(
        body: LoginInput, request: Request, response: Response
    ) -> LoginResponse:
        """校验来源和限速后建立会话，重复登录替换本浏览器旧会话。"""
        require_same_origin(request)
        consume_attempt(engine, request.client.host if request.client else "unknown")
        try:
            with engine.begin() as conn:
                token = login(conn, body.username, body.password.get_secret_value())
                logout(conn, request.cookies.get(COOKIE, ""))
        except AuthenticationError:
            raise HTTPException(401, "登录失败") from None
        response.set_cookie(
            COOKIE,
            token,
            max_age=28800,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return LoginResponse()

    @app.post("/api/v1/auth/logout", status_code=204)
    def sign_out(request: Request) -> Response:
        """幂等撤销会话并清除浏览器 Cookie。"""
        require_same_origin(request)
        with engine.begin() as conn:
            logout(conn, request.cookies.get(COOKIE, ""))
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/api/v1/auth/me", response_model=CurrentUserResponse)
    def current_user(request: Request, response: Response) -> CurrentUserResponse:
        """根据 Cookie 认证身份并重新计算可见账户，不接受客户端身份 ID。"""
        try:
            with engine.begin() as conn:
                user = authenticate(conn, request.cookies.get(COOKIE, ""))
                accounts = [
                    VisibleAccount(id=row[0], name=row[1])
                    for row in conn.execute(visible_accounts(user))
                ]
        except AuthenticationError:
            raise HTTPException(401, "请先登录") from None
        response.headers["Cache-Control"] = "no-store"
        return CurrentUserResponse(user_id=user, accounts=accounts)
