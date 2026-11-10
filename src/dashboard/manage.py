"""本机账号维护命令：首次管理员初始化和密码重置。

通过 python -m dashboard.manage bootstrap|reset --username 登录名 执行。
仅访问 V2 受限运行目录中的本机数据库配置；密码两次隐藏输入，禁止命令行
传密码。维护操作与事件记录同事务提交，失败回滚。此工具需要本机受限文件
访问权，是本地维护入口，不作为普通业务用户的改密接口或生产恢复流程。
"""

import argparse
import getpass
import warnings

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from dashboard.auth import hash_password, utc_now
from dashboard.database import make_engine
from dashboard.local_config import load_maintenance_database_settings


def update_account(
    conn: Connection, action: str, username: str, password: str, operator: str
) -> int:
    """在调用方事务内创建首个管理员或重置密码，写入最小操作记录。"""
    if (
        action not in {"bootstrap", "reset"}
        or not 1 <= len(username) <= 100
        or not 1 <= len(operator) <= 100
    ):
        raise ValueError("操作、登录名或维护者信息无效")
    password_hash = hash_password(password)
    if action == "bootstrap":
        # 调用入口持有命名维护锁，避免两个初始化进程同时创建首个账号。
        if conn.execute(text("SELECT COUNT(*) FROM auth_credentials")).scalar_one():
            raise ValueError("已有登录账号，不能再次初始化管理员")
        conn.execute(
            text(
                "INSERT INTO access_users (name,role,department_id,active) VALUES (:name,'admin',NULL,1)"
            ),
            {"name": username},
        )
        user_id = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        conn.execute(
            text(
                "INSERT INTO auth_credentials (user_id,login,password_hash) VALUES (:user,:login,:hash)"
            ),
            {"user": user_id, "login": username, "hash": password_hash},
        )
    else:
        # 和登录共享凭据行锁：旧密码验证与重置不能交错创建遗留会话。
        user = conn.execute(
            text("SELECT user_id FROM auth_credentials WHERE login=:login FOR UPDATE"),
            {"login": username},
        ).scalar()
        if user is None:
            raise ValueError("登录账号不存在")
        user_id = int(user)
        conn.execute(
            text("UPDATE auth_credentials SET password_hash=:hash WHERE user_id=:user"),
            {"hash": password_hash, "user": user_id},
        )
        conn.execute(
            text("DELETE FROM auth_sessions WHERE user_id=:user"), {"user": user_id}
        )
    conn.execute(
        text(
            "INSERT INTO auth_account_events (user_id,action,operator,created_at) VALUES (:user,:action,:operator,:created)"
        ),
        {"user": user_id, "action": action, "operator": operator, "created": utc_now()},
    )
    return user_id


def main() -> int:
    """读取隐藏密码后串行执行本地维护命令，不打印底层数据库异常。"""
    parser = argparse.ArgumentParser(description="V2 本机账号维护")
    parser.add_argument("action", choices=["bootstrap", "reset"])
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        # 先校验连接配置，再提示密码；不在命令模块解析秘密或创建另一条连接路径。
        settings = load_maintenance_database_settings()
        # 无法隐藏输入的终端直接拒绝，避免退回到明文回显。
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("请输入密码：")
            if password != getpass.getpass("请再次输入密码："):
                raise ValueError("两次密码不一致")
        engine = make_engine(settings)
        try:
            with engine.connect() as conn:
                locked = conn.execute(
                    text("SELECT GET_LOCK('onetitle_v2_account_maintenance', 10)")
                ).scalar()
                conn.commit()
                if locked != 1:
                    raise ValueError("其他维护操作正在执行，请稍后重试")
                try:
                    with conn.begin():
                        update_account(
                            conn,
                            args.action,
                            args.username,
                            password,
                            getpass.getuser(),
                        )
                finally:
                    conn.execute(
                        text("SELECT RELEASE_LOCK('onetitle_v2_account_maintenance')")
                    )
                    conn.commit()
        finally:
            engine.dispose()
    except (OSError, ValueError, KeyError, SQLAlchemyError, getpass.GetPassWarning):
        print("操作失败，请检查输入、迁移状态与本地配置；未输出敏感信息。")
        return 2
    print("操作完成；密码重置会撤销该账号全部会话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
