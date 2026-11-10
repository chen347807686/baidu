"""来源账户映射的隔离 MySQL 约束验证。

使用随机测试库和合成账户，验证稳定身份唯一、名称不唯一、二进制区分、
外键与状态校验及空表回退。不读取真实映射或密钥，不操作运行库。
"""

import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError, OperationalError

from dashboard.database import load_database_settings, make_engine
from dashboard.schema import account_sources


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_source_account_constraints() -> None:
    """同名不合并、同来源 ID 不重复，非空映射不得降级删除。"""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    engine = make_engine(load_database_settings())
    now = datetime(2026, 11, 10)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('合成内部账户')")
            )
            account = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        values = {
            "account_id": account,
            "source_namespace": "baidu-test",
            "external_account_id": "AbC",
            "source_account_name": "同名账户",
            "channel": "paid_baidu_search",
            "active": True,
            "mapping_version": 1,
            "created_at": now,
            "updated_at": now,
        }
        with engine.begin() as conn:
            conn.execute(insert(account_sources), values)
            conn.execute(
                insert(account_sources), dict(values, external_account_id="abc")
            )
            conn.execute(
                insert(account_sources), dict(values, source_namespace="other-test")
            )
        invalid_changes: list[dict[str, object]] = [
            {"external_account_id": "AbC"},
            {"account_id": -999},
            {"channel": "Paid_Baidu_Search"},
            {"mapping_version": 0},
            {"source_namespace": ""},
        ]
        for changes in invalid_changes:
            with (
                pytest.raises((IntegrityError, OperationalError)),
                engine.begin() as conn,
            ):
                # 每个无效样本使用未占用身份，避免重复键掩盖其他约束缺失。
                conn.execute(
                    insert(account_sources),
                    dict(values, external_account_id="new-id") | changes,
                )
        with pytest.raises((IntegrityError, OperationalError)), engine.begin() as conn:
            # 绕过 SQLAlchemy Boolean 转换，直接验证数据库 CHECK 而非客户端拒绝。
            conn.execute(
                text(
                    "UPDATE account_sources SET active=2 WHERE external_account_id='AbC'"
                )
            )
        with pytest.raises(RuntimeError, match="非空"):
            command.downgrade(config, "0010")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM account_sources"))
        command.downgrade(config, "0010")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
