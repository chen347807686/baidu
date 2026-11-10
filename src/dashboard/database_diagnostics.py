"""数据库错误的最小脱敏诊断，供受控本机导入入口使用。

只记录固定操作名、SQLAlchemy 异常类别、数值 MySQL 错误码和合法 SQLSTATE。
不序列化异常，不记录 SQL、绑定参数、连接地址或异常堆栈。错误分类只用于
排查提示，不能证明事务提交或回滚结果；断线后仍需按主键或计划状态核对。
默认交给 Python 日志系统输出到本机标准错误，不自动写文件或配置全局日志。
"""

import logging
import re
from typing import Literal

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

LOGGER = logging.getLogger(__name__)

# 仅按已知 MySQL 数字码分类，绝不通过异常消息提取表名、用户名或业务内容。
ERROR_CATEGORIES = {
    "schema": {1049, 1054, 1146},
    "permission": {1044, 1045, 1142, 1143, 1227},
    "constraint": {1048, 1062, 1264, 1406, 1451, 1452, 3819},
    "concurrency": {1205, 1213},
    "connection": {2002, 2003, 2006, 2013, 2055},
}
HINTS = {
    "schema": "数据库结构错误，请检查目标库和迁移状态",
    "permission": "数据库权限或认证失败，请检查维护账号授权",
    "constraint": "数据库约束或字段范围校验失败，请核对映射与输入",
    "concurrency": "数据库锁等待或死锁，请核对状态后重试",
    "connection": "数据库连接异常，请检查本机实例与连接状态",
    "unknown": "数据库操作异常，请检查本机诊断记录",
}


def report_database_error(
    exc: SQLAlchemyError,
    operation: Literal["keyword_batch", "import_plan", "import_apply", "import_status"],
) -> str:
    """记录白名单字段并返回安全提示；不从错误类型推断最终提交结果。"""
    code: int | None = None
    state: str | None = None
    # DBAPIError.orig 可能含业务数据，只取严格限制类型和范围的独立字段。
    if isinstance(exc, DBAPIError):
        args = getattr(exc.orig, "args", ())
        if args and type(args[0]) is int and 1000 <= args[0] <= 9999:
            code = args[0]
        candidate = getattr(exc.orig, "sqlstate", None)
        if isinstance(candidate, str) and re.fullmatch(r"[A-Z0-9]{5}", candidate):
            state = candidate
    category = next(
        (name for name, codes in ERROR_CATEGORIES.items() if code in codes), "unknown"
    )
    # 按 SQLAlchemy 内置基类识别类别，不输出调用方自定义异常类名。
    exception_type = next(
        (
            cls.__name__
            for cls in type(exc).__mro__
            if cls.__module__ == "sqlalchemy.exc"
        ),
        "SQLAlchemyError",
    )
    LOGGER.warning(
        "database_error operation=%s category=%s exception=%s mysql_code=%s sqlstate=%s",
        operation,
        category,
        exception_type,
        code,
        state,
    )
    return HINTS[category]
