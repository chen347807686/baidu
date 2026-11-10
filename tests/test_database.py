"""独立本机 MySQL 的迁移与权限集成测试。

通过 local_mysql.py test 注入测试凭据后运行，否则跳过本测试。
验证连接不建表、迁移可重复执行、应用账号可查询但不能创建表。
断言覆盖当前权限结构，新增业务迁移时需要同步更新验收预期。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from dashboard.database import load_database_settings, make_engine


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"),
    reason="Run scripts/local_mysql.py test against the isolated local database",
)
def test_mysql_migration_and_permissions() -> None:
    """在专用本地库验证迁移边界与应用账号权限，并始终释放连接池。"""
    settings = load_database_settings()
    # 限定测试目标，避免将结构验证误用于其它环境或业务库。
    assert settings.host == "127.0.0.1"
    assert settings.name.startswith("tv2test")
    engine = make_engine(settings)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        # 单纯建立连接和执行查询不得触发任何隐式建表操作。
        before = inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        assert inspect(engine).get_table_names() == before
        # 连续升级两次，确认已应用版本不会被重复执行。
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0008"
            )
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "access_departments",
            "access_users",
            "access_accounts",
            "access_account_grants",
            "access_department_grants",
            "auth_credentials",
            "auth_sessions",
            "auth_login_limits",
            "auth_account_events",
            "report_sample_daily",
            "keyword_daily_v2",
            "keyword_import_plans",
        }
        # 改用运行时账号验证最小权限，不能用迁移账号代替应用访问。
        app_settings = settings.model_copy(
            update={
                "user": os.environ["ONETITLE_TEST_APP_USER"],
                "password": SecretStr(os.environ["ONETITLE_TEST_APP_PASSWORD"]),
            }
        )
        app_engine = make_engine(app_settings)
        try:
            with app_engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT COUNT(*) FROM alembic_version")) == 1
                )
                with pytest.raises(DBAPIError):
                    connection.execute(text("CREATE TABLE forbidden_ddl (id INTEGER)"))
        finally:
            app_engine.dispose()
    finally:
        engine.dispose()
