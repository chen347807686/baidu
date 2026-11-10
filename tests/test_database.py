import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from dashboard.database import load_database_settings, make_engine


@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_APP_PASSWORD"),
    reason="Run scripts/local_mysql.py test against the isolated local database",
)
def test_mysql_migration_and_permissions() -> None:
    settings = load_database_settings()
    assert settings.host == "127.0.0.1"
    assert settings.name == "onetitle_v2_local"
    engine = make_engine(settings)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        before = inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        assert inspect(engine).get_table_names() == before
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0001"
            )
        assert inspect(engine).get_table_names() == ["alembic_version"]
        app_settings = settings.model_copy(
            update={
                "user": "v2_app",
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
