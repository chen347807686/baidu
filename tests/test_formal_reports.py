"""正式日报查询及来源分离的隔离 MySQL 测试。

在同一测试事务内构造正式日报和合成日报，核对聚合指标、未知转化保留、
账户权限及来源不混用；结束回滚，测试库仍由公共夹具销毁。
"""

import os
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard.daily_reports import keyword_daily_report
from dashboard.database import load_database_settings, make_engine
from dashboard.import_preview import load_rows
from dashboard.keyword_input import prepare_keyword_rows
from dashboard.keyword_store import UPSERT
from dashboard.reports import keyword_report


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_formal_and_synthetic_sources() -> None:
    """按授权账户聚合正式事实，切换来源不能串入另一张表的记录。"""
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    engine = make_engine(load_database_settings())
    day = date(2026, 11, 9)
    raw = dict(load_rows(root / "docs/example-keywords.json")[0])
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(
                    text(
                        "INSERT INTO access_users (id,name,role,active) VALUES (-80,'正式查询','bidder',1),(-81,'隔离账户','bidder',1)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO access_accounts (name,owner_id) VALUES ('授权账户',-80)"
                    )
                )
                account = int(
                    conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
                )
                conn.execute(
                    text(
                        "INSERT INTO access_accounts (name,owner_id) VALUES ('无权账户',-81)"
                    )
                )
                other = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
                for target, record in [
                    (account, dict(raw, conv_78=7, conv_92=9)),
                    (account, dict(raw, device="mobile", cost="2.0000", click=5)),
                    (other, dict(raw, cost="999.0000")),
                ]:
                    item = prepare_keyword_rows(
                        [record], {"synthetic-preview": target}
                    )[0]
                    conn.execute(UPSERT, dict(item.row.model_dump(), account_id=target))
                conn.execute(
                    text(
                        "INSERT INTO report_sample_daily (day,account_id,keyword,cost,impression,click,conv_form,conv_lead,conv_dial,conv_wechat,conv_1sent,conv_3sent) VALUES (:day,:account,'合成独有',3,0,0,0,0,0,0,0,0)"
                    ),
                    {"day": day, "account": account},
                )
                result = keyword_report(
                    conn,
                    -80,
                    day,
                    day,
                    None,
                    "",
                    "cost",
                    "desc",
                    1,
                    50,
                    source="formal",
                )
                assert result["data_source"] == "formal" and result["total"] == 1
                assert (
                    result["summary"]["cost"] == "14.3400"
                    and result["summary"]["click"] == 15
                )
                assert (
                    result["rows"][0]["conv_78"] == 7
                    and result["rows"][0]["conv_92"] == 9
                )
                assert result["rows"][0]["lead_total"] == 2
                # 每日下钻与列表同口径，多设备相加，无权账户不进入每日聚合。
                detail = keyword_daily_report(conn, -80, day, day, str(raw["keyword"]))
                assert len(detail.rows) == 1
                assert detail.rows[0].cost == "14.3400"
                assert detail.rows[0].click == 15 and detail.rows[0].lead_total == 2
                assert detail.rows[0].conv_78 == 7 and detail.rows[0].conv_92 == 9
                with pytest.raises(PermissionError):
                    keyword_daily_report(
                        conn, -80, day, day, str(raw["keyword"]), other
                    )
                assert (
                    keyword_daily_report(conn, -999, day, day, str(raw["keyword"])).rows
                    == []
                )
                assert (
                    keyword_daily_report(conn, -80, day, day, "不存在的精确词").rows
                    == []
                )
                # 新日期独立分组、日期升序；没有记录的中间日期不补零。
                later = dict(raw, date="2026-11-11", cost="3.0000", click=0)
                item = prepare_keyword_rows([later], {"synthetic-preview": account})[0]
                conn.execute(UPSERT, dict(item.row.model_dump(), account_id=account))
                daily = keyword_daily_report(
                    conn, -80, day, date(2026, 11, 11), str(raw["keyword"])
                )
                assert [row.date for row in daily.rows] == ["2026-11-09", "2026-11-11"]
                assert daily.rows[1].cpc is None
                assert daily.missing_dates == "omitted"
                for start, end in [(day, date(2026, 11, 8)), (day, date(2027, 11, 10))]:
                    with pytest.raises(ValueError):
                        keyword_daily_report(conn, -80, start, end, str(raw["keyword"]))
                synthetic = keyword_report(
                    conn,
                    -80,
                    day,
                    day,
                    None,
                    "",
                    "cost",
                    "desc",
                    1,
                    50,
                    source="synthetic",
                )
                assert (
                    synthetic["summary"]["cost"] == "3.0000"
                    and synthetic["rows"][0]["keyword"] == "合成独有"
                )
                assert "conv_78" not in synthetic["rows"][0]
                with pytest.raises(PermissionError):
                    keyword_report(
                        conn,
                        -80,
                        day,
                        day,
                        other,
                        "",
                        "cost",
                        "desc",
                        1,
                        50,
                        source="formal",
                    )
                assert (
                    keyword_report(
                        conn,
                        -999,
                        day,
                        day,
                        None,
                        "",
                        "cost",
                        "desc",
                        1,
                        50,
                        source="formal",
                    )["total"]
                    == 0
                )
                with pytest.raises(ValueError):
                    keyword_report(
                        conn,
                        -80,
                        day,
                        day,
                        None,
                        "",
                        "cost",
                        "desc",
                        1,
                        50,
                        source="arbitrary-table",
                    )
            finally:
                tx.rollback()
    finally:
        engine.dispose()
