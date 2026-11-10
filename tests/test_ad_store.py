"""受控投放写入的隔离 MySQL 回归，使用合成来源与规范输入。

覆盖重复覆盖、核验顺序、映射版本与启停，以及后行拒绝导致前行回滚。
不连接百度、不写运行库，不将本地验证当作生产截止时间或采集完整性验收。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from dashboard.ad_input import prepare_ad_rows
from dashboard.ad_store import AdWriteError, store_ad_batch
from dashboard.database import load_database_settings, make_engine
from dashboard.schema import ad_daily
from dashboard.source_manage import maintain_source


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_ad_batch_write() -> None:
    """真实提交验证幂等、NULL 覆盖、拒绝旧写及批内事务回滚。"""
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(load_database_settings())
    now = datetime(2026, 11, 10, tzinfo=UTC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('合成写入账户')")
            )
            account = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            source, _ = maintain_source(
                conn,
                operator="synthetic",
                account_id=account,
                namespace="test",
                external_id="write",
                name="合成来源",
            )
        raw: dict[str, object] = {
            "account_id": account,
            "source_account_id": source,
            "stat_date": "2026-11-10",
            "report_kind": "keyword",
            "project_id": None,
            "campaign_id": 1,
            "adgroup_id": 2,
            "keyword_id": 3,
            "creative_id": None,
            "keyword_text": "合成词",
            "search_term": None,
            "entity_name": None,
            "device": "pc",
            "device_scope": "detail",
            "extra_dimensions": {},
            "impressions": 100,
            "clicks": 10,
            "cost": "12.3400",
            "source_revision": "r1",
            "source_checked_at": now,
            "ingested_at": now,
        }
        assert store_ad_batch(engine, [raw], {source: 1}).inserted == 1
        assert store_ad_batch(engine, [raw], {source: 1}).unchanged == 1
        corrected = raw | {
            "cost": None,
            "clicks": 5,
            "keyword_text": "更正词",
            "source_checked_at": now + timedelta(minutes=1),
            "source_revision": "r2",
        }
        assert store_ad_batch(engine, [corrected], {source: 1}).updated == 1
        with engine.connect() as conn:
            row = conn.execute(select(ad_daily)).mappings().one()
            assert (
                row["cost"] is None
                and row["clicks"] == 5
                and row["keyword_text"] == "更正词"
            )
        for rejected in [raw, corrected | {"clicks": 6}]:
            with pytest.raises(AdWriteError):
                store_ad_batch(engine, [rejected], {source: 1})
        with pytest.raises(AdWriteError):
            store_ad_batch(engine, [corrected], {source: 2})
        # 指纹排序由数据决定，确保第一条实际执行后第二条才拒绝，而不是猜顺序。
        candidates = [
            raw | {"keyword_id": value, "source_checked_at": now + timedelta(minutes=2)}
            for value in (8, 9)
        ]
        candidates.sort(key=lambda value: str(prepare_ad_rows([value])[0].grain_key))
        assert store_ad_batch(engine, [candidates[1]], {source: 1}).inserted == 1
        invalid_second = candidates[1] | {"source_checked_at": now}
        with pytest.raises(AdWriteError):
            store_ad_batch(engine, [candidates[0], invalid_second], {source: 1})
        with engine.connect() as conn:
            assert len(conn.execute(select(ad_daily)).all()) == 2
        with engine.begin() as conn:
            maintain_source(
                conn,
                operator="synthetic",
                source_id=source,
                expected_version=1,
                active=False,
            )
        with pytest.raises(AdWriteError):
            store_ad_batch(engine, [corrected], {source: 2})
        with pytest.raises(AdWriteError):
            store_ad_batch(engine, [raw] * 11, {source: 1})
        assert store_ad_batch(engine, [], {}).inserted == 0
    finally:
        engine.dispose()
