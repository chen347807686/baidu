"""正式日报表默认及名称列排序规则迁移回归。

在默认 ai_ci 的隔离库中验证迁移前实际默认、迁移后 as_cs 和三列二进制
规则保持不变；明确测试名称大小写/重音区分，不改变存储文本。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard.database import load_database_settings, make_engine


@pytest.mark.mysql
@pytest.mark.mysql_collation("utf8mb4_0900_ai_ci")
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_keyword_table_collation_migration() -> None:
    """表默认必须显式固定；角色安全结构和关键词二进制列不被转换。"""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0009")
    engine = make_engine(load_database_settings())
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='keyword_daily_v2'"
                    )
                ).scalar_one()
                == "utf8mb4_0900_ai_ci"
            )
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='keyword_daily_v2'"
                    )
                ).scalar_one()
                == "utf8mb4_0900_as_cs"
            )
            collations: dict[str, str | None] = dict(
                conn.execute(
                    text(
                        "SELECT COLUMN_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='keyword_daily_v2'"
                    )
                ).all()
            )
            assert all(
                collations[name] == "utf8mb4_0900_bin"
                for name in ("keyword", "device", "user_name")
            )
            assert all(
                collations[name] == "utf8mb4_0900_as_cs"
                for name in ("campaign_name", "adgroup_name")
            )
            assert conn.execute(
                text(
                    "SELECT (_utf8mb4'Plan' COLLATE utf8mb4_0900_as_cs = _utf8mb4'plan'),(_utf8mb4'Plan' COLLATE utf8mb4_0900_as_cs = _utf8mb4'Plán')"
                )
            ).one() == (0, 0)
        command.downgrade(config, "0009")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='access_users' AND COLUMN_NAME='role'"
                    )
                ).scalar_one()
                == "utf8mb4_0900_bin"
            )
        command.upgrade(config, "head")
    finally:
        engine.dispose()
