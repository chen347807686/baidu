"""合成关键词报表的真实 MySQL 验证。

仅在本机测试库事务内写入合成账户和日报，结束全部回滚。验证账户权限先于
聚合、零分母、字面搜索和稳定分页，不使用真实投放数据。数据库由公共夹具
按测试独立创建，不再依赖本地运行库或其它测试的数据状态。
"""

import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard.database import load_database_settings, make_engine
from dashboard.reports import keyword_report, metrics


def test_zero_metrics() -> None:
    """零数据不能伪造成本、点击单价或转化率。"""
    result = metrics({})
    assert result["cost"] == "0.0000"
    assert result["lead_cost"] is None and result["cpc"] is None
    assert result["consult_rate"] is None and result["lead_rate"] is None


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要独立本机 MySQL"
)
def test_keyword_report() -> None:
    """验证合成口径、账户隔离、参数拒绝和数据库分页。"""
    settings = load_database_settings()
    assert settings.host == "127.0.0.1" and settings.name.startswith("tv2test")
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(settings)
    day = date(2026, 11, 9)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(
                    text(
                        "INSERT INTO access_users (id,name,role,department_id,active) VALUES (-40,'报表甲','bidder',NULL,1),(-41,'报表乙','bidder',NULL,1)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO access_accounts (id,name,owner_id,department_id) VALUES (-40,'合成甲',-40,NULL),(-41,'合成乙',-41,NULL)"
                    )
                )
                statement = text(
                    "INSERT INTO report_sample_daily (day,account_id,keyword,cost,impression,click,conv_form,conv_lead,conv_dial,conv_wechat,conv_1sent,conv_3sent) VALUES (:day,:account,:keyword,:cost,100,:click,:form,:lead,:dial,0,:consult,0)"
                )
                for account, keyword, cost, click, form, lead, dial, consult in [
                    (-40, "同词", 100, 10, 1, 0, 0, 2),
                    (-40, "同词", 50, 5, 0, 1, 1, 1),
                    (-41, "同词", 999, 99, 9, 9, 9, 9),
                    (-40, "100%_词", 150, 0, 0, 0, 0, 0),
                    (-40, "Case", 0, 0, 0, 0, 0, 0),
                    (-40, "case", 0, 0, 0, 0, 0, 0),
                ]:
                    conn.execute(
                        statement,
                        {
                            "day": day,
                            "account": account,
                            "keyword": keyword,
                            "cost": cost,
                            "click": click,
                            "form": form,
                            "lead": lead,
                            "dial": dial,
                            "consult": consult,
                        },
                    )

                def query(search: str = "", page: int = 1) -> dict[str, Any]:
                    """每次使用相同数据快照，以一行页验证稳定次序。"""
                    return keyword_report(
                        conn, -40, day, day, None, search, "cost", "desc", page, 1
                    )

                match = query("同词")
                row = match["rows"][0]
                assert row["cost"] == "150.0000" and row["click"] == 15
                assert row["lead_total"] == 3 and row["consult_rate"] == "0.2000"
                assert row["lead_cost"] == "50.00" and row["cpc"] == "10.0000"
                assert query()["total"] == 4
                assert query()["summary"]["cost"] == "300.0000"
                first, second = (
                    query()["rows"][0]["keyword"],
                    query(page=2)["rows"][0]["keyword"],
                )
                assert first != second and first == query()["rows"][0]["keyword"]
                assert query("%_")["total"] == 1
                assert query("' OR 1=1 --")["total"] == 0
                assert query("case")["total"] == 1
                assert query(page=99)["rows"] == [] and query(page=99)["total"] == 4
                with pytest.raises(PermissionError):
                    keyword_report(conn, -40, day, day, -41, "", "cost", "desc", 1, 50)
                assert (
                    keyword_report(
                        conn, -999, day, day, None, "", "cost", "desc", 1, 50
                    )["total"]
                    == 0
                )
                with pytest.raises(ValueError):
                    keyword_report(
                        conn,
                        -40,
                        day,
                        date(2026, 11, 8),
                        None,
                        "",
                        "cost",
                        "desc",
                        1,
                        50,
                    )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
