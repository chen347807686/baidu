"""统一投放日报结构的真实 MySQL 隔离验证。

仅在随机测试库构造来源映射和事实，验证可空指标、复合粒度与来源绑定，
不模拟已完成百度采集或聚合服务。所有数据均为合成，不触碰运行库。
"""

import os
from datetime import date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from dashboard.database import load_database_settings, make_engine
from dashboard.schema import ad_daily
from dashboard.source_manage import maintain_source


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_ad_fact_constraints() -> None:
    """报表类型、设备与日期保留独立行，未知不同于零，来源不得跨账户。"""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    engine = make_engine(load_database_settings())
    now = datetime(2026, 11, 10)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('投放合成账户')")
            )
            account = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            source, _ = maintain_source(
                conn,
                operator="synthetic",
                account_id=account,
                namespace="test",
                external_id="ad-001",
                name="合成来源",
            )
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('另一个合成账户')")
            )
            other = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        values: dict[str, object] = {
            "account_id": account,
            "stat_date": date(2026, 11, 9),
            "report_kind": "keyword",
            "grain_key": b"a" * 32,
            "source_account_id": source,
            "grain_version": 1,
            "device_scope": "unknown",
            "dimensions": {"schema_version": 1},
            "source_revision": "synthetic-1",
            "source_checked_at": now,
            "content_hash": b"b" * 32,
            "ingested_at": now,
        }
        with engine.begin() as conn:
            conn.execute(insert(ad_daily), values)
            conn.execute(
                insert(ad_daily),
                values | {"report_kind": "search_term", "cost": "0.0000", "clicks": 0},
            )
            conn.execute(insert(ad_daily), values | {"report_kind": "creative"})
            conn.execute(
                insert(ad_daily),
                values
                | {"grain_key": b"c" * 32, "device_scope": "detail", "device": "pc"},
            )
            rows = (
                conn.execute(select(ad_daily).order_by(ad_daily.c.report_kind))
                .mappings()
                .all()
            )
            assert len(rows) == 4
            zero = next(row for row in rows if row["report_kind"] == "search_term")
            assert str(zero["cost"]) == "0.0000" and zero["clicks"] == 0
            assert all(
                row["cost"] is None
                for row in rows
                if row["report_kind"] != "search_term"
            )
        invalid: list[dict[str, object]] = [
            {},
            {"report_kind": "Keyword"},
            {"device_scope": "Detail"},
            {"grain_version": 0},
            {"cost": "-1.0000"},
            {"clicks": -1},
            {"account_id": other},
        ]
        for changes in invalid:
            # 原身份只测试重复键；其余用新指纹，避免重复键掩盖约束失败。
            candidate = (
                values if not changes else values | {"grain_key": b"z" * 32} | changes
            )
            with pytest.raises(DBAPIError), engine.begin() as conn:
                conn.execute(insert(ad_daily), candidate)
        with pytest.raises(RuntimeError, match="非空"):
            command.downgrade(config, "0012")
        with engine.begin() as conn:
            conn.execute(ad_daily.delete())
        command.downgrade(config, "0012")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
