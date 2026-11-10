"""确认入库的隔离 MySQL 验证，全部使用合成文件和动态账户。

验证生成计划不写事实、明确确认、失败整批回滚、重试与文件/数据库变化检测。
异常注入仅针对测试 SQL；测试库由公共夹具清理，不修改本地管理员。
"""

import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from dashboard.database import load_database_settings, make_engine
from dashboard.import_execute import apply_plan, create_plan, plan_status


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_confirmed_import(tmp_path: Path) -> None:
    """执行和批次状态必须一起提交，旧预览不能覆盖变化后的数据。"""
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    engine = make_engine(load_database_settings())
    source, mapping = tmp_path / "rows.json", tmp_path / "mapping.json"
    raw = json.loads((root / "docs/example-keywords.json").read_text(encoding="utf-8"))[
        0
    ]
    content = json.dumps([raw, dict(raw, w_info_id=302)])
    source.write_text(content, encoding="utf-8")
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('确认入库样本')")
            )
            account = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        mapping.write_text(json.dumps({"synthetic-preview": account}), encoding="utf-8")
        with pytest.raises(ValueError):
            create_plan(engine, source, mapping, "")
        plan = create_plan(engine, source, mapping, "synthetic decimal fixture")
        assert plan["summary"] == {"new": 2, "changed": 0, "unchanged": 0}
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT COUNT(*) FROM keyword_daily_v2")).scalar_one()
                == 0
            )
        with pytest.raises(ValueError):
            apply_plan(engine, source, mapping, plan["plan_id"], "wrong")
        writes = 0

        def fail_second(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            """模拟写到第二行失败，检查第一行和计划状态都回滚。"""
            nonlocal writes
            if statement.startswith("INSERT INTO keyword_daily_v2"):
                writes += 1
                if writes == 2:
                    raise SQLAlchemyError("synthetic failure")

        event.listen(engine, "before_cursor_execute", fail_second)
        try:
            with pytest.raises(SQLAlchemyError):
                apply_plan(
                    engine, source, mapping, plan["plan_id"], plan["confirmation"]
                )
        finally:
            event.remove(engine, "before_cursor_execute", fail_second)
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT COUNT(*) FROM keyword_daily_v2")).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text("SELECT status FROM keyword_import_plans WHERE id=:id"),
                    {"id": plan["plan_id"]},
                ).scalar_one()
                == "planned"
            )
        assert (
            apply_plan(engine, source, mapping, plan["plan_id"], plan["confirmation"])[
                "processed"
            ]
            == 2
        )
        assert (
            apply_plan(engine, source, mapping, plan["plan_id"], plan["confirmation"])[
                "replayed"
            ]
            is True
        )
        status = plan_status(engine, plan["plan_id"])
        assert status["status"] == "applied" and status["processed"] == 2
        assert "confirmation" not in status
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT COUNT(*) FROM keyword_daily_v2")).scalar_one()
                == 2
            )
        source.write_text(content + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="输入已变化"):
            apply_plan(engine, source, mapping, plan["plan_id"], plan["confirmation"])
        source.write_text(content, encoding="utf-8")
        stale = create_plan(engine, source, mapping, "synthetic decimal fixture")
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE keyword_daily_v2 SET click=9 WHERE w_info_id=301")
            )
        with pytest.raises(ValueError, match="已变化"):
            apply_plan(engine, source, mapping, stale["plan_id"], stale["confirmation"])
        expired = create_plan(engine, source, mapping, "synthetic decimal fixture")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE keyword_import_plans SET expires_at='2000-01-01' WHERE id=:id"
                ),
                {"id": expired["plan_id"]},
            )
        with pytest.raises(ValueError, match="过期"):
            apply_plan(
                engine, source, mapping, expired["plan_id"], expired["confirmation"]
            )
        with engine.begin() as conn:
            conn.execute(text("UPDATE keyword_daily_v2 SET user_name='renamed-source'"))
        with pytest.raises(ValueError, match="歧义"):
            create_plan(engine, source, mapping, "synthetic decimal fixture")
    finally:
        engine.dispose()
