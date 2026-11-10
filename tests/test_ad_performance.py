"""投放微批的显式性能测量，仅在随机隔离 MySQL 库启用。

需 ONETITLE_RUN_AD_BENCHMARK=1，普通回归跳过。比较同/不同账户及单/四线程，
报告端到端 P50/P95、吞吐与 SQL 次数，不设机器相关速度门槛；不将结果等同
生产容量或直接的锁等待时长。只使用合成数据，退出由公共夹具清理资源。
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text

from dashboard.ad_store import store_ad_batch
from dashboard.database import load_database_settings, make_engine
from dashboard.schema import ad_daily
from dashboard.source_manage import maintain_source


@pytest.mark.mysql
@pytest.mark.skipif(
    os.environ.get("ONETITLE_RUN_AD_BENCHMARK") != "1", reason="显式性能测量"
)
def test_ad_microbatch_benchmark() -> None:
    """每场景十二批十行，使用不同事实键，验证提交行数后输出脱敏测量。"""
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(load_database_settings())
    pairs: list[tuple[int, int]] = []
    reports: list[dict[str, object]] = []
    try:
        with engine.begin() as conn:
            for number in range(4):
                conn.execute(
                    text("INSERT INTO access_accounts (name) VALUES ('性能合成账户')")
                )
                account = int(
                    conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
                )
                source, _ = maintain_source(
                    conn,
                    operator="benchmark",
                    account_id=account,
                    namespace="synthetic",
                    external_id=str(number),
                    name="性能来源",
                )
                pairs.append((account, source))
        for scenario, (workers, same_account) in enumerate(
            [(1, True), (4, True), (4, False)]
        ):
            statements = 0
            counter_lock = threading.Lock()

            def count_sql(
                conn: object,
                cursor: object,
                statement: str,
                parameters: object,
                context: object,
                executemany: bool,
            ) -> None:
                """仅计数，不记录语句或参数；不含驱动提交与连接握手。"""
                nonlocal statements
                with counter_lock:  # noqa: B023 -- 当前场景线程全部结束后才进入下一个场景。
                    statements += 1

            def run_batch(
                batch: int, same: bool = same_account, scenario_id: int = scenario
            ) -> float:
                """固定规范数据，耗时包含准备、连接池等待、事务与提交。"""
                account, source = pairs[0 if same else batch % 4]
                now = datetime(2026, 11, 10, tzinfo=UTC)
                rows: list[dict[str, object]] = []
                for offset in range(10):
                    rows.append(
                        {
                            "account_id": account,
                            "source_account_id": source,
                            "stat_date": "2026-11-10",
                            "report_kind": "keyword",
                            "project_id": None,
                            "campaign_id": 1,
                            "adgroup_id": 1,
                            "keyword_id": scenario_id * 1000 + batch * 10 + offset,
                            "creative_id": None,
                            "keyword_text": "性能合成词",
                            "search_term": None,
                            "entity_name": None,
                            "device": "pc",
                            "device_scope": "detail",
                            "extra_dimensions": {},
                            "impressions": 100,
                            "clicks": 10,
                            "cost": "12.5000",
                            "source_revision": "r1",
                            "source_checked_at": now,
                            "ingested_at": now,
                        }
                    )
                started = perf_counter()
                assert store_ad_batch(engine, rows, {source: 1}).inserted == 10
                return perf_counter() - started

            # 先建立连接池，避免把首次连接成本全部归给第一个测量场景。
            with ThreadPoolExecutor(max_workers=4) as pool:

                def ping(number: int) -> None:
                    """只读连接预热，不写事实。"""
                    with engine.connect() as conn:
                        conn.execute(text("SELECT 1"))

                list(pool.map(ping, range(4)))
            event.listen(engine, "before_cursor_execute", count_sql)
            try:
                started = perf_counter()
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    durations = sorted(pool.map(run_batch, range(12)))
                elapsed = perf_counter() - started
            finally:
                event.remove(engine, "before_cursor_execute", count_sql)
            reports.append(
                {
                    "workers": workers,
                    "same_account": same_account,
                    "rows": 120,
                    "rows_per_second": round(120 / elapsed, 1),
                    "batch_p50_ms": round(durations[5] * 1000, 2),
                    "batch_p95_ms": round(durations[11] * 1000, 2),
                    "sql_per_batch": statements / 12,
                }
            )
        with engine.connect() as conn:
            assert (
                conn.execute(select(func.count()).select_from(ad_daily)).scalar_one()
                == 360
            )
        print("AD_BENCHMARK=" + json.dumps(reports))
    finally:
        engine.dispose()
