"""共享运行时结构与冻结迁移的漂移验证。

在隔离 MySQL 空库执行历史迁移，比较共享表的列类型、可空性、外键、
索引、主键、显式排序规则和现有 CHECK。另改动模型副本确认测试能发现漂移，
不修改数据库或全局模型。其它认证/计划表暂不在本次共享 metadata 范围内。
"""

import os
import re
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Boolean, CheckConstraint, MetaData, Numeric, inspect, text
from sqlalchemy.dialects.mysql import TINYINT

from dashboard.database import load_database_settings, make_engine
from dashboard.keyword_input import KeywordRow
from dashboard.schema import KEYWORD_FIELDS, KEYWORD_KEYS, keyword_daily, metadata


def check_tokens(expression: str) -> list[str]:
    """规范当前纯 AND/IN 约束的反射包装；若未来引入 OR 必须升级比较策略。"""
    expression = re.sub(r"_utf8mb4(?=')", "", expression).replace("`", "")
    tokens = re.findall(
        r"'[^']*'|[A-Za-z_][A-Za-z0-9_]*|>=|<=|<>|\d+|=|>|<|,", expression
    )
    result = [token if token.startswith("'") else token.lower() for token in tokens]
    assert "or" not in result, "新增 OR 约束需要保留括号语义的比较策略"
    return result


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_migrations_match_shared_schema() -> None:
    """冻结迁移重放后的结构必须匹配模型，模型副本漂移也必须被检测。"""
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(load_database_settings())
    try:
        with engine.connect() as conn:

            def include_object(
                obj: Any, name: str, kind: str, reflected: bool, compare_to: Any
            ) -> bool:
                """只核对本批收口的表，不把暂未纳管的表当作待删除结构。"""
                return kind != "table" or name in metadata.tables

            def compare_type(
                context: Any,
                inspected: Any,
                model: Any,
                actual_type: Any,
                expected_type: Any,
            ) -> bool | None:
                """MySQL BOOL 实际存为 TINYINT(1)，仅允许这一等价映射。"""
                if isinstance(expected_type, Boolean):
                    return not (
                        isinstance(actual_type, TINYINT)
                        and actual_type.display_width == 1
                    )
                return None

            context = MigrationContext.configure(
                conn,
                opts={
                    "include_object": include_object,
                    "compare_type": compare_type,
                    "compare_server_default": True,
                },
            )
            assert compare_metadata(context, metadata) == []
            inspector = inspect(conn)
            for name, model in metadata.tables.items():
                expected_table_collation = model.dialect_options["mysql"].get("collate")
                if expected_table_collation:
                    actual: str = conn.execute(
                        text(
                            "SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
                        ),
                        {"table": name},
                    ).scalar_one()
                    assert actual == expected_table_collation
                assert inspector.get_pk_constraint(name)["constrained_columns"] == list(
                    model.primary_key.columns.keys()
                )
                actual_checks = {
                    item["name"]: check_tokens(item["sqltext"])
                    for item in inspector.get_check_constraints(name)
                }
                expected_checks = {
                    str(item.name): check_tokens(str(item.sqltext))
                    for item in model.constraints
                    if isinstance(item, CheckConstraint)
                }
                assert actual_checks == expected_checks
                actual_collations: dict[str, str | None] = dict(
                    conn.execute(
                        text(
                            "SELECT COLUMN_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
                        ),
                        {"table": name},
                    ).all()
                )
                for col in model.c:
                    expected_collation = getattr(col.type, "collation", None)
                    if expected_collation is not None:
                        assert actual_collations[col.name] == expected_collation
            copied = MetaData()
            for model in metadata.sorted_tables:
                model.to_metadata(copied)
            copied.tables[keyword_daily.name].c.cost.type = Numeric(18, 2)
            assert compare_metadata(context, copied), "金额精度漂移必须被发现"
            assert set(KEYWORD_FIELDS) == set(KeywordRow.model_fields) | {"account_id"}
            assert KEYWORD_KEYS == (
                "date",
                "account_id",
                "campaign_id",
                "adgroup_id",
                "w_info_id",
                "device",
            )
    finally:
        engine.dispose()
