"""本地 HTTP 服务的命令行启动入口。

通过 python -m dashboard 调用：校验环境配置、创建应用，再启动 Uvicorn。
配置错误仅输出经过脱敏的字段提示并返回退出码 2；数据库迁移需单独执行。
"""

import sys

import uvicorn

from dashboard.app import create_app
from dashboard.config import ConfigurationError, load_settings


def main() -> int:
    """启动服务，正常结束返回 0，配置不合法时返回 2。"""
    try:
        settings = load_settings()
        # 应用注册路由与服务器监听共用同一份已校验配置，避免环境被重复读取。
        app = create_app(settings)
    except ConfigurationError as exc:
        # 此异常由配置入口统一脱敏，禁止在这里输出原始环境变量。
        print(str(exc), file=sys.stderr)
        return 2
    # 本地配置限制为回环地址；关闭访问日志，避免默认记录请求内容。
    # 本机阶段不信任代理转发头，防止伪造来源地址绕过登录限速。
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        access_log=False,
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
