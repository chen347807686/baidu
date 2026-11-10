"""本机来源维护的隔离事务与版本回归。

仅用临时库和合成账户，验证创建、更新、旧版本拒绝、无变化不写事件与
事件失败回滚映射；CLI 无确认时不得读取维护凭据。不触及真实来源或运行库。
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError

from dashboard.database import load_database_settings, make_engine
from dashboard.schema import account_sources, source_events
from dashboard.source_inspect import inspect_events, inspect_sources
from dashboard.source_inspect import main as inspect_main
from dashboard.source_manage import main, maintain_source


def test_confirmation_before_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """无显式确认时不接触数据库或凭据。"""
    monkeypatch.setattr(sys, "argv", ["sources", "create"])
    loader = MagicMock(side_effect=AssertionError("不得读取凭据"))
    monkeypatch.setattr(
        "dashboard.source_manage.load_maintenance_database_settings", loader
    )
    assert main() == 2
    loader.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [["events"], ["list", "--limit", "101"], ["list", "--namespace", "test"]],
)
def test_invalid_inspection_before_credentials(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """无界分页或不完整身份核对在读取凭据前拒绝。"""
    monkeypatch.setattr(sys, "argv", ["inspect", *arguments])
    loader = MagicMock(side_effect=AssertionError("不得读取凭据"))
    monkeypatch.setattr(
        "dashboard.source_inspect.load_maintenance_database_settings", loader
    )
    assert inspect_main() == 2
    loader.assert_not_called()


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_source_maintenance() -> None:
    """版本和审计共同提交，更新事件失败时不能留下部分更改。"""
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(load_database_settings())
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO access_accounts (name) VALUES ('合成维护账户')")
            )
            account = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
            identifier, version = maintain_source(
                conn,
                operator="synthetic",
                account_id=account,
                namespace="test",
                external_id="abc",
                name="PRIVATE_NAME",
            )
            assert version == 1
        with engine.begin() as conn:
            assert maintain_source(
                conn,
                operator="synthetic",
                source_id=identifier,
                expected_version=1,
                name="PRIVATE_NAME",
            ) == (identifier, 1)
            assert maintain_source(
                conn,
                operator="synthetic",
                source_id=identifier,
                expected_version=1,
                active=False,
                name="PRIVATE_RENAMED",
            ) == (identifier, 2)
            records = conn.execute(select(source_events)).mappings().all()
            assert len(records) == 2
            assert records[1]["changed_fields"] == ["active", "source_account_name"]
            assert "PRIVATE" not in str(records)
        with pytest.raises(ValueError), engine.begin() as conn:
            maintain_source(
                conn,
                operator="synthetic",
                source_id=identifier,
                expected_version=1,
                active=True,
            )
        with pytest.raises(ValueError), engine.begin() as conn:
            maintain_source(
                conn,
                operator="synthetic",
                source_id=identifier,
                expected_version=2,
                account_id=account,
            )

        def fail_audit(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            """只针对事件插入注入错误，检查先前映射更新随事务回滚。"""
            if statement.startswith("INSERT INTO account_source_events"):
                raise SQLAlchemyError("合成审计失败")

        event.listen(engine, "before_cursor_execute", fail_audit)
        try:
            with pytest.raises(SQLAlchemyError), engine.begin() as conn:
                maintain_source(
                    conn,
                    operator="synthetic",
                    source_id=identifier,
                    expected_version=2,
                    active=True,
                )
        finally:
            event.remove(engine, "before_cursor_execute", fail_audit)
        with engine.connect() as conn:
            row = (
                conn.execute(
                    select(account_sources).where(account_sources.c.id == identifier)
                )
                .mappings()
                .one()
            )
            assert row["mapping_version"] == 2 and not row["active"]
            assert len(conn.execute(select(source_events)).all()) == 2
            mappings = inspect_sources(conn, namespace="test", external_id="abc")
            assert len(mappings) == 1 and mappings[0]["mapping_version"] == 2
            assert mappings[0]["active"] is False
            assert "PRIVATE" not in str(mappings)
            assert "credential_ref" not in mappings[0]
            assert inspect_sources(conn, namespace="test", external_id="ABC") == []
            assert inspect_sources(conn, after_id=identifier) == []
            first = inspect_events(conn, identifier, limit=1)
            cursor = first[0]["id"]
            assert isinstance(cursor, int)
            second = inspect_events(conn, identifier, after_id=cursor)
            assert len(first) == len(second) == 1
            assert (
                first[0]["mapping_version"] == 1 and second[0]["mapping_version"] == 2
            )
        # 显式只读事务仍可核对，维护状态和事件数量不变。
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            assert len(inspect_sources(conn)) == 1
            assert len(inspect_events(conn, identifier)) == 2
    finally:
        engine.dispose()
