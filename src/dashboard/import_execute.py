"""本机受控导入计划与显式确认执行，不提供网页写入入口。

文件内容先一次读取再解析，计划绑定文件/映射/候选/数据库状态指纹及金额核对
依据摘要。计划一小时过期；确认时重读文件、锁定计划及目标账户/事实范围。
发现变化或改名歧义就拒绝，写入与成功状态同事务提交。失败不输出原始数据。
"""

import argparse
import csv
import getpass
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, Engine, bindparam, select, text
from sqlalchemy.exc import SQLAlchemyError

from dashboard.auth import utc_now
from dashboard.database import load_database_settings, make_engine
from dashboard.database_diagnostics import report_database_error
from dashboard.import_preview import load_rows, parse_json, read_text
from dashboard.keyword_input import PreparedKeyword, prepare_keyword_rows
from dashboard.keyword_store import validate_keyword_batch, write_keyword_rows
from dashboard.schema import KEYWORD_KEYS as KEYS
from dashboard.schema import keyword_daily as facts


def digest(value: object) -> str:
    """以确定顺序序列化指纹，不把被指纹化的业务内容输出。"""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def read_candidates(
    source: Path, mapping_path: Path
) -> tuple[list[PreparedKeyword], str, str, str]:
    """从同一份内容快照解析并计算文件摘要，避免校验与指纹读取两次。"""
    source_text, mapping_text = read_text(source), read_text(mapping_path)
    mapping = parse_json(mapping_text)
    if not isinstance(mapping, dict):
        raise TypeError("账户映射格式无效")
    rows = prepare_keyword_rows(load_rows(source, content=source_text), mapping)
    validate_keyword_batch(rows)
    if not rows:
        raise ValueError("不能为零行数据创建计划")
    rows_hash = digest(
        [dict(item.row.model_dump(), account_id=item.account_id) for item in rows]
    )
    return rows, digest(source_text), digest(mapping_text), rows_hash


def locked_state(
    conn: Connection, rows: list[PreparedKeyword]
) -> tuple[str, dict[str, int]]:
    """锁定账户和其事实范围；保守比较整个账户，最多检查一万条已有事实。"""
    ids = sorted({item.account_id for item in rows})
    accounts = (
        conn.execute(
            text(
                "SELECT id,name,owner_id,department_id FROM access_accounts WHERE id IN :ids ORDER BY id FOR UPDATE"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
        .mappings()
        .all()
    )
    if len(accounts) != len(ids):
        raise ValueError("目标账户不存在，需重新预览")
    existing = (
        conn.execute(
            select(facts)
            .where(facts.c.account_id.in_(ids))
            .order_by(*[facts.c[key] for key in KEYS])
            .limit(10001)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    if len(existing) > 10000:
        raise ValueError("现有数据超过本地计划核对上限")
    names = {item.account_id: item.row.user_name for item in rows}
    if any(record["user_name"] != names[record["account_id"]] for record in existing):
        raise ValueError("源账户名称存在歧义，需业务核对")
    existing_by_key = {
        tuple(record[key] for key in KEYS): dict(record) for record in existing
    }
    summary = {"new": 0, "changed": 0, "unchanged": 0}
    for item in rows:
        expected = dict(item.row.model_dump(), account_id=item.account_id)
        status = (
            "new"
            if item.key not in existing_by_key
            else "unchanged"
            if existing_by_key[item.key] == expected
            else "changed"
        )
        summary[status] += 1
    state = digest(
        {
            "accounts": [dict(value) for value in accounts],
            "facts": [dict(value) for value in existing],
        }
    )
    return state, summary


def create_plan(
    engine: Engine, source: Path, mapping_path: Path, amount_reference: str
) -> dict[str, Any]:
    """仅保存计划元数据，不写事实；金额来源必须由维护者先独立核实。"""
    if not 1 <= len(amount_reference.strip()) <= 200:
        raise ValueError("必须提供有效金额核对依据")
    rows, input_hash, mapping_hash, rows_hash = read_candidates(source, mapping_path)
    plan_id = uuid.uuid4().hex
    now = utc_now()
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        state_hash, summary = locked_state(conn, rows)
        reference_hash = digest(amount_reference)
        confirmation = digest(
            [plan_id, input_hash, mapping_hash, rows_hash, state_hash, reference_hash]
        )
        conn.execute(
            text(
                "INSERT INTO keyword_import_plans (id,input_hash,mapping_hash,rows_hash,state_hash,confirmation,amount_reference_hash,status,processed,created_at,expires_at,summary,created_by) VALUES (:id,:input,:mapping,:rows,:state,:confirmation,:reference,'planned',0,:created,:expires,:summary,:operator)"
            ),
            {
                "id": plan_id,
                "input": input_hash,
                "mapping": mapping_hash,
                "rows": rows_hash,
                "state": state_hash,
                "summary": json.dumps(summary),
                "operator": getpass.getuser()[:100],
                "confirmation": confirmation,
                "reference": reference_hash,
                "created": now,
                "expires": now + timedelta(hours=1),
            },
        )
    return {
        "plan_id": plan_id,
        "confirmation": confirmation,
        "rows": len(rows),
        "status": "planned",
        "summary": summary,
        "expires_in_seconds": 3600,
        "notice": "仅生成计划，尚未写入日报；执行需显式确认",
    }


def apply_plan(
    engine: Engine, source: Path, mapping_path: Path, plan_id: str, confirmation: str
) -> dict[str, Any]:
    """重新校验并显式执行计划，重复确认同一成功计划只返回原结果。"""
    rows, input_hash, mapping_hash, rows_hash = read_candidates(source, mapping_path)
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        plan = (
            conn.execute(
                text("SELECT * FROM keyword_import_plans WHERE id=:id FOR UPDATE"),
                {"id": plan_id},
            )
            .mappings()
            .first()
        )
        if plan is None or not hmac.compare_digest(
            str(plan["confirmation"]), confirmation
        ):
            raise ValueError("计划或确认指纹无效")
        if (input_hash, mapping_hash, rows_hash) != (
            plan["input_hash"],
            plan["mapping_hash"],
            plan["rows_hash"],
        ):
            raise ValueError("输入已变化，请重新预览")
        if plan["status"] == "applied":
            return {
                "plan_id": plan_id,
                "status": "applied",
                "processed": plan["processed"],
                "replayed": True,
            }
        if plan["status"] != "planned" or plan["expires_at"] <= utc_now():
            raise ValueError("计划已过期或状态无效")
        if locked_state(conn, rows)[0] != plan["state_hash"]:
            raise ValueError("目标数据或账户已变化，请重新预览")
        # 共用写入入口不提交事务，确保事实和下方计划成功状态共同提交或回滚。
        write_keyword_rows(conn, rows)
        conn.execute(
            text(
                "UPDATE keyword_import_plans SET status='applied',processed=:count,applied_at=:now,applied_by=:operator WHERE id=:id"
            ),
            {
                "count": len(rows),
                "now": utc_now(),
                "id": plan_id,
                "operator": getpass.getuser()[:100],
            },
        )
    return {
        "plan_id": plan_id,
        "status": "applied",
        "processed": len(rows),
        "replayed": False,
    }


def plan_status(engine: Engine, plan_id: str) -> dict[str, Any]:
    """只读查询计划结果，用于提交超时后的核对，不返回源数据或确认指纹。"""
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT id,status,processed,created_at,expires_at,applied_at,summary FROM keyword_import_plans WHERE id=:id"
                ),
                {"id": plan_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("计划不存在")
        result = dict(row)
        result["summary"] = json.loads(result["summary"])
        return result


def main() -> int:
    """本机命令行维护入口；无默认执行，出错须按计划状态核对提交结果。"""
    parser = argparse.ArgumentParser(description="本地关键词导入计划与显式执行")
    parser.add_argument("action", choices=["plan", "apply", "status"])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--amount-reference")
    parser.add_argument("--plan-id")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    engine = None
    try:
        settings = load_database_settings()
        from dashboard.local_config import require_local_database

        require_local_database(settings)
        engine = make_engine(settings)
        if args.action == "status" and args.plan_id:
            result = plan_status(engine, args.plan_id)
        elif not args.input or not args.mapping:
            raise ValueError("计划和执行必须提供文件及映射")
        elif args.action == "plan":
            result = create_plan(
                engine, args.input, args.mapping, args.amount_reference or ""
            )
        elif args.plan_id and args.confirm:
            result = apply_plan(
                engine, args.input, args.mapping, args.plan_id, args.confirm
            )
        else:
            raise ValueError("执行必须提供计划和确认指纹")
    except SQLAlchemyError as exc:
        operations: dict[
            str, Literal["import_plan", "import_apply", "import_status"]
        ] = {
            "plan": "import_plan",
            "apply": "import_apply",
            "status": "import_status",
        }
        hint = report_database_error(exc, operations[args.action])
        print(hint + "；操作未确认成功，请按计划 ID 核对状态；未回显业务数据。")
        return 2
    except (
        OSError,
        ValueError,
        TypeError,
        ArithmeticError,
        csv.Error,
        RecursionError,
    ):
        print(
            "操作未确认成功，请检查文件、依据及计划状态；提交异常需按计划 ID 核对，未回显业务数据。"
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
