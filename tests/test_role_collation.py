"""角色大小写安全的真实 MySQL 回归，数据库默认使用不敏感规则。

先在旧版本重现合法 CHECK 接受 Admin 的条件，验证新查询拒绝授权、迁移停止
且不改数据；核对后升级，再验证新约束拒绝混合大小写和空白角色。测试只操作
随机隔离库，不修正或重置实际管理员。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from dashboard.access import visible_account_ids, visible_accounts
from dashboard.database import load_database_settings, make_engine


@pytest.mark.mysql
@pytest.mark.mysql_collation("utf8mb4_0900_ai_ci")
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_role_guard_and_migration_preflight() -> None:
    """旧库也不误授权；迁移先阻断歧义数据，再显式改变角色列排序规则。"""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0008")
    engine = make_engine(load_database_settings())
    try:
        with engine.begin() as conn:
            assert (
                conn.execute(text("SELECT @@collation_database")).scalar_one()
                == "utf8mb4_0900_ai_ci"
            )
            conn.execute(
                text(
                    "INSERT INTO access_users (id,name,role,active) VALUES (1,'规范管理员','admin',1),(2,'混合角色','Admin',1)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO access_accounts (id,name) VALUES (1,'未分配合成账户')"
                )
            )
            assert list(conn.execute(visible_account_ids(1)).scalars()) == [1]
            assert list(conn.execute(visible_account_ids(2)).scalars()) == []
            assert conn.execute(visible_accounts(2)).all() == []
        with pytest.raises(RuntimeError, match="非规范角色") as caught:
            command.upgrade(config, "head")
        assert "Admin" not in str(caught.value)
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "0008"
            )
            assert (
                conn.execute(
                    text("SELECT role FROM access_users WHERE id=2")
                ).scalar_one()
                == "Admin"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='access_users' AND COLUMN_NAME='role'"
                    )
                ).scalar_one()
                == "utf8mb4_0900_ai_ci"
            )
            # 仅对测试合成歧义行明确删除，模拟人工核对；不将其归为管理员。
            conn.execute(text("DELETE FROM access_users WHERE id=2"))
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='access_users' AND COLUMN_NAME='role'"
                    )
                ).scalar_one()
                == "utf8mb4_0900_bin"
            )
            assert list(conn.execute(visible_account_ids(1)).scalars()) == [1]
            for role in (
                "Admin",
                "ADMIN",
                "admin ",
                "admin\t",
                "Manager",
                "Bidder",
                "ａｄｍｉｎ",
            ):
                with pytest.raises(DBAPIError) as rejected:
                    conn.execute(
                        text(
                            "INSERT INTO access_users (name,role,active) VALUES ('非法角色样本',:role,1)"
                        ),
                        {"role": role},
                    )
                # PyMySQL 将 MySQL CHECK 3819 归为 OperationalError，而非 IntegrityError。
                assert rejected.value.orig is not None
                assert rejected.value.orig.args[0] == 3819
            assert (
                conn.execute(text("SELECT COUNT(*) FROM access_users")).scalar_one()
                == 1
            )
        # 禁止静默降级回不安全排序规则，迁移版本与结构应保留。
        with pytest.raises(RuntimeError, match="禁止直接撤销"):
            command.downgrade(config, "0008")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "0009"
            )
    finally:
        engine.dispose()
