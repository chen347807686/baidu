"""正式日报写入的真实 MySQL 集成验证。

动态创建独立合成用户与账户，测试真实提交、重复导入、名称/指标更正及整批
回滚。结束仅清理本测试分配的 ID，不使用真实验证管理员；旧测试隔离问题
仍保留，本模块不依赖固定账户 ID 或空凭据库。
"""

import os
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard.database import load_database_settings, make_engine
from dashboard.keyword_input import KeywordInputError, prepare_keyword_rows
from dashboard.keyword_store import (
    KeywordWriteError,
    store_keyword_batch,
    write_keyword_rows,
)


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要本机独立 MySQL"
)
def test_formal_keyword_upsert() -> None:
    """重复同键覆盖而非累加，外键失败时同批先前写入必须回滚。"""
    settings = load_database_settings()
    assert settings.host == "127.0.0.1" and settings.name.startswith("tv2test")
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(settings)
    user_id = account_id = None
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO access_users (name,role,active) VALUES ('正式日报合成测试','bidder',1)"
                )
            )
            user_id = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            conn.execute(
                text(
                    "INSERT INTO access_accounts (name,owner_id) VALUES ('日报合成账户',:owner)"
                ),
                {"owner": user_id},
            )
            account_id = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        raw: dict[str, object] = {
            name: 0
            for name in (
                "impression",
                "click",
                "conv_form",
                "conv_3sent",
                "conv_lead",
                "conv_1sent",
                "conv_dial",
                "conv_wechat",
                "conv_78",
                "conv_92",
            )
        }
        raw.update(
            date="2026-11-09",
            user_name="synthetic-store",
            campaign_id=1,
            campaign_name="计划",
            adgroup_id=2,
            adgroup_name="单元",
            w_info_id=3,
            keyword="合成词",
            device="pc",
            cost="12.3456",
            conv_78=7,
            conv_92=9,
        )
        batch = prepare_keyword_rows([raw], {"synthetic-store": account_id})
        assert store_keyword_batch(engine, batch).processed == 1
        assert store_keyword_batch(engine, batch).processed == 1
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT cost,conv_78,conv_92 FROM keyword_daily_v2 WHERE account_id=:id"
                ),
                {"id": account_id},
            ).all()
            assert len(result) == 1 and str(result[0][0]) == "12.3456"
            assert result[0][1:] == (7, 9)
        corrected = prepare_keyword_rows(
            [dict(raw, cost="15.0000", keyword="改名词", click=5)],
            {"synthetic-store": account_id},
        )
        store_keyword_batch(engine, corrected)
        with engine.connect() as conn:
            changed = conn.execute(
                text(
                    "SELECT cost,click FROM keyword_daily_v2 WHERE account_id=:id AND w_info_id=3 AND device='pc'"
                ),
                {"id": account_id},
            ).one()
            assert str(changed[0]) == "15.0000" and changed[1] == 5
        # 共用入口即使成功执行，也不能提前提交调用方事务。模拟后续业务失败，
        # 确认已写入的更正随外层事务一起回滚，保留之前已提交的金额。
        with (
            pytest.raises(RuntimeError, match="模拟后续业务失败"),
            engine.begin() as conn,
        ):
            write_keyword_rows(conn, batch)
            raise RuntimeError("模拟后续业务失败")
        with engine.connect() as conn:
            assert (
                str(
                    conn.execute(
                        text(
                            "SELECT cost FROM keyword_daily_v2 WHERE account_id=:id AND w_info_id=3 AND device='pc'"
                        ),
                        {"id": account_id},
                    ).scalar_one()
                )
                == "15.0000"
            )
        distinct = prepare_keyword_rows(
            [dict(raw, device="mobile"), dict(raw, w_info_id=4)],
            {"synthetic-store": account_id},
        )
        store_keyword_batch(engine, distinct)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT COUNT(*) FROM keyword_daily_v2 WHERE account_id=:id"),
                    {"id": account_id},
                ).scalar_one()
                == 3
            )
            assert (
                conn.execute(
                    text(
                        "SELECT keyword FROM keyword_daily_v2 WHERE account_id=:id AND device='pc' AND w_info_id=3"
                    ),
                    {"id": account_id},
                ).scalar_one()
                == "改名词"
            )
        # 在独立库中分配后删除一个真实 ID，确定该外键目标不存在，不猜固定大数。
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('待删除的外键样本')")
            )
            missing_account = int(
                conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
            )
            conn.execute(
                text("DELETE FROM access_accounts WHERE id=:id"),
                {"id": missing_account},
            )
        new = prepare_keyword_rows(
            [dict(raw, w_info_id=99)], {"synthetic-store": account_id}
        )[0]
        with pytest.raises(KeywordWriteError):
            store_keyword_batch(engine, [new, replace(new, account_id=missing_account)])
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM keyword_daily_v2 WHERE account_id=:id AND w_info_id=99"
                    ),
                    {"id": account_id},
                ).scalar_one()
                == 0
            )
        with pytest.raises(KeywordInputError, match="主键重复"):
            store_keyword_batch(engine, [new, new])
        floating = prepare_keyword_rows(
            [dict(raw, cost=1.23)],
            {"synthetic-store": account_id},
            allow_legacy_float=True,
        )
        with pytest.raises(KeywordInputError, match="来源尚未核实"):
            store_keyword_batch(engine, floating)
        assert store_keyword_batch(engine, []).processed == 0
    finally:
        # 无论断言或写入何处失败，都只清理动态分配的合成数据。
        with engine.begin() as conn:
            if account_id is not None:
                conn.execute(
                    text("DELETE FROM keyword_daily_v2 WHERE account_id=:id"),
                    {"id": account_id},
                )
                conn.execute(
                    text("DELETE FROM access_accounts WHERE id=:id"), {"id": account_id}
                )
            if user_id is not None:
                conn.execute(
                    text("DELETE FROM access_users WHERE id=:id"), {"id": user_id}
                )
        engine.dispose()
