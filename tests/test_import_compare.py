"""数据库只读预览验证，使用逐测试随机 MySQL 库。

构造新增、覆盖、未变化、账户缺失与源名称变化的合成样本，检查分类和字段
提示，并核对预览前后事实行完全一致。SQL 观察用于确认只发送读取语句。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text

from dashboard.database import load_database_settings, make_engine
from dashboard.import_compare import compare_keyword_batch
from dashboard.import_preview import load_rows
from dashboard.keyword_input import prepare_keyword_rows
from dashboard.keyword_store import store_keyword_batch


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_database_preview_is_read_only() -> None:
    """同一快照正确分类，不输出源名称/旧值，不改变原始记录。"""
    root = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    engine = make_engine(load_database_settings())
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO access_accounts (name) VALUES ('合成预览')"))
            account = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            conn.execute(text("INSERT INTO access_accounts (name) VALUES ('缺失样本')"))
            missing = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            conn.execute(
                text("DELETE FROM access_accounts WHERE id=:id"), {"id": missing}
            )
        raw = dict(load_rows(root / "docs/example-keywords.json")[0])
        mapping = {"synthetic-preview": account}
        stored = prepare_keyword_rows([raw, dict(raw, w_info_id=302)], mapping)
        store_keyword_batch(engine, stored)
        incoming = prepare_keyword_rows(
            [raw, dict(raw, w_info_id=302, cost="20.0000"), dict(raw, w_info_id=303)],
            mapping,
        )
        with engine.connect() as conn:
            before = conn.execute(
                text("SELECT * FROM keyword_daily_v2 ORDER BY w_info_id")
            ).all()
        statements: list[str] = []

        def observe(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            """只记录语句类型，不记录绑定值或秘密。"""
            statements.append(statement.split()[0].upper())

        event.listen(engine, "before_cursor_execute", observe)
        result = compare_keyword_batch(engine, incoming)
        assert result["counts"] == {
            "new": 1,
            "changed": 1,
            "unchanged": 1,
            "missing_account": 0,
            "account_alias_review": 0,
        }
        assert result["details"][1]["changed_fields"] == ["cost"]
        assert result["write_allowed"] is False and set(statements) <= {"SET", "SELECT"}
        assert "synthetic-preview" not in str(result) and "合成预览词" not in str(
            result
        )
        event.remove(engine, "before_cursor_execute", observe)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT * FROM keyword_daily_v2 ORDER BY w_info_id")
                ).all()
                == before
            )
        renamed = prepare_keyword_rows(
            [dict(raw, user_name="renamed-source")], {"renamed-source": account}
        )
        assert (
            compare_keyword_batch(engine, renamed)["counts"]["account_alias_review"]
            == 1
        )
        absent = prepare_keyword_rows([raw], {"synthetic-preview": missing})
        assert compare_keyword_batch(engine, absent)["counts"]["missing_account"] == 1
        assert compare_keyword_batch(engine, [])["database_checked"] is False
    finally:
        engine.dispose()
