"""真实 MySQL 测试的逐测试隔离环境。

标记 mysql 的集成测试显式提供本机测试管理员密码后，为每项测试创建
随机独立库和随机迁移/应用账号；测试结束只删除本夹具成功创建的资源。
本地验证管理员及运行库从不作为测试目标。强杀进程时 finally 无法保证执行，
可能留下 tv2test 随机库，但后续测试不会复用它；残留清理需独立核对后处理。
"""

import os
import re
import secrets
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import URL, create_engine, text


@pytest.fixture(autouse=True)
def isolated_mysql(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """集成测试用临时逻辑库，普通纯函数测试不连接 MySQL。"""
    if request.node.get_closest_marker("mysql") is None:
        yield
        return
    password = os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD")
    if not password:
        pytest.skip("需通过 local_mysql.py test 显式启用隔离 MySQL 测试")
    suffix = uuid.uuid4().hex
    database = "tv2test" + suffix
    migrator, app = "tv2m" + suffix[:16], "tv2a" + suffix[:16]
    migration_password, app_password = secrets.token_hex(24), secrets.token_hex(24)
    # 只允许固定本机测试实例，忽略运行环境的 ONETITLE_DB_* 目标配置。
    admin = create_engine(
        URL.create(
            "mysql+pymysql",
            username="root",
            password=password,
            host="127.0.0.1",
            port=13306,
        ),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    created_database = False
    created_users: list[str] = []
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs"
                )
            )
            created_database = True
            for user, secret, permissions in (
                (migrator, migration_password, "ALL"),
                (app, app_password, "SELECT,INSERT,UPDATE,DELETE"),
            ):
                # 标识符来自本夹具生成的十六进制值；密码作为绑定值传递。
                conn.execute(
                    text(f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY :password"),
                    {"password": secret},
                )
                created_users.append(user)
                conn.execute(
                    text(
                        f"GRANT {permissions} ON `{database}`.* TO '{user}'@'127.0.0.1'"
                    )
                )
        for key, value in {
            "ONETITLE_DB_NAME": database,
            "ONETITLE_DB_HOST": "127.0.0.1",
            "ONETITLE_DB_PORT": "13306",
            "ONETITLE_DB_USER": migrator,
            "ONETITLE_DB_PASSWORD": migration_password,
            "ONETITLE_TEST_APP_USER": app,
            "ONETITLE_TEST_APP_PASSWORD": app_password,
        }.items():
            monkeypatch.setenv(key, value)
        yield
    finally:
        # 名称必须严格属于本次随机资源，创建失败时不删除任何既有资源。
        assert re.fullmatch(r"tv2test[a-f0-9]{32}", database)
        try:
            with admin.connect() as conn:
                if created_database:
                    conn.execute(text(f"DROP DATABASE `{database}`"))
                for user in created_users:
                    assert re.fullmatch(r"tv2[ma][a-f0-9]{16}", user)
                    conn.execute(text(f"DROP USER '{user}'@'127.0.0.1'"))
        finally:
            admin.dispose()
