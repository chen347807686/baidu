"""数据库诊断的脱敏与调用入口回归，不连接真实数据库。

构造携带敏感标记的驱动错误，验证只保留白名单错误码和 SQLSTATE，日志与
对外提示均不泄漏异常原文、SQL 或参数。模拟本地导入 CLI 与批次失败，
不改变真实数据库或全局日志配置。
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from dashboard import import_execute
from dashboard.database import DatabaseSettings
from dashboard.database_diagnostics import report_database_error
from dashboard.keyword_input import prepare_keyword_rows
from dashboard.keyword_store import KeywordWriteError, store_keyword_batch


class DriverError(Exception):
    """合成驱动异常，原文故意包含不得出现在日志里的数据。"""

    sqlstate = "42S02"


def database_error(code: object = 1146) -> OperationalError:
    """同时污染 SQL、参数和异常消息，覆盖三种常见泄漏来源。"""
    return OperationalError(
        "PRIVATE_SQL",
        {"secret": "PRIVATE_PARAMETER"},
        DriverError(code, "PRIVATE_DATA"),
    )


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (1146, "schema"),
        (1142, "permission"),
        (1452, "constraint"),
        (1213, "concurrency"),
        (2013, "connection"),
        (9999, "unknown"),
    ],
)
def test_safe_error_categories(
    code: int, category: str, caplog: pytest.LogCaptureFixture
) -> None:
    """不同故障提供可诊断分类，但绝不记录驱动消息或 SQL。"""
    hint = report_database_error(database_error(code), "keyword_batch")
    assert f"category={category}" in caplog.text
    assert f"mysql_code={code}" in caplog.text
    assert "exception=OperationalError" in caplog.text
    assert "sqlstate=42S02" in caplog.text
    assert "PRIVATE" not in caplog.text + hint
    assert all(record.exc_info is None for record in caplog.records)


def test_reject_untrusted_diagnostic_fields(caplog: pytest.LogCaptureFixture) -> None:
    """错误码不转字符串、不解析消息，非法 SQLSTATE 直接丢弃。"""
    exc = database_error("PRIVATE_CODE")
    exc.orig.sqlstate = "PRIVATE_STATE"  # type: ignore[union-attr]
    report_database_error(exc, "import_apply")
    report_database_error(SQLAlchemyError("PRIVATE_MESSAGE"), "import_status")
    assert "PRIVATE" not in caplog.text
    assert "mysql_code=None sqlstate=None" in caplog.text


def test_batch_failure_is_logged_and_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """直接小批次保留安全业务异常，同时留下可排查的数据库错误码。"""
    engine = MagicMock()
    engine.begin.side_effect = database_error()
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "docs/example-keywords.json").read_text(encoding="utf-8"))
    rows = prepare_keyword_rows(raw, {"synthetic-preview": 1})
    with pytest.raises(KeywordWriteError, match="数据库结构错误") as caught:
        store_keyword_batch(engine, rows)
    assert "PRIVATE" not in str(caught.value) + caplog.text
    assert "operation=keyword_batch" in caplog.text


@pytest.mark.parametrize("action", ["plan", "apply", "status"])
def test_cli_database_failure(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """三个维护操作都捕获数据库异常，提示核对状态并记录安全操作标识。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import",
            action,
            "--input",
            "synthetic.json",
            "--mapping",
            "mapping.json",
            "--plan-id",
            "synthetic-plan",
            "--confirm",
            "synthetic-confirmation",
        ],
    )
    settings = DatabaseSettings(
        name="onetitle_v2_local",
        user="synthetic",
        password=SecretStr("PRIVATE_PASSWORD"),
    )
    monkeypatch.setattr(import_execute, "load_database_settings", lambda: settings)
    engine = MagicMock()
    monkeypatch.setattr(import_execute, "make_engine", lambda settings: engine)
    operation = {"plan": "create_plan", "apply": "apply_plan", "status": "plan_status"}
    failing_operation = MagicMock(side_effect=database_error(2013))
    monkeypatch.setattr(import_execute, operation[action], failing_operation)
    assert import_execute.main() == 2
    output = capsys.readouterr().out
    assert "数据库连接异常" in output and "按计划 ID 核对" in output
    assert f"operation=import_{action}" in caplog.text
    assert "PRIVATE" not in output + caplog.text
    failing_operation.assert_called_once()
    engine.dispose.assert_called_once()
