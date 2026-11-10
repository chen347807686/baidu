"""本地密码认证及持久会话的服务层。

密码使用随机盐与 PBKDF2-HMAC-SHA256 哈希；会话使用高熵随机令牌，仅摘要
进入数据库。每次认证会话重新检查用户启用状态，不保存角色或账户权限快照。
本模块不处理 HTTP、Cookie、限速或账号管理授权；调用方负责提交事务，
并将认证得到的用户 ID 传给统一权限策略。当前仅用于本地验证。
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

ITERATIONS = 600_000
# 未知登录名仍执行相同成本的校验，减少明显的用户存在性时间差。
DUMMY_HASH = "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32


class AuthenticationError(ValueError):
    """统一认证失败，不向调用方区分用户不存在、停用或密码错误。"""


def hash_password(password: str) -> str:
    """生成带随机盐的哈希，限制输入长度以约束资源消耗。"""
    if not 12 <= len(password) <= 128:
        raise ValueError("密码长度必须为 12 至 128 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """验证哈希格式并用恒定时间比较摘要；非法记录一律拒绝。"""
    if len(password) > 128:
        return False
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt_bytes, ITERATIONS
        )
        return hmac.compare_digest(actual, expected_bytes)
    except (ValueError, TypeError):
        return False


def utc_now() -> datetime:
    """返回 UTC 无时区对象，与 MySQL DATETIME 的存储约定一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


def login(connection: Connection, username: str, password: str) -> str:
    """验证凭据并建立八小时会话，原始令牌仅返回给调用方一次。"""
    row = (
        connection.execute(
            text(
                "SELECT c.user_id, c.password_hash, u.active FROM auth_credentials c "
                "JOIN access_users u ON u.id=c.user_id WHERE c.login=:login"
            ),
            {"login": username},
        )
        .mappings()
        .first()
    )
    valid = verify_password(password, str(row["password_hash"]) if row else DUMMY_HASH)
    if row is None or not valid or not row["active"]:
        raise AuthenticationError("登录失败")
    token = secrets.token_urlsafe(32)
    connection.execute(
        text(
            "INSERT INTO auth_sessions (token_hash,user_id,expires_at) VALUES (:token,:user,:expires)"
        ),
        {
            "token": hashlib.sha256(token.encode()).hexdigest(),
            "user": row["user_id"],
            "expires": utc_now() + timedelta(hours=8),
        },
    )
    return token


def authenticate(connection: Connection, token: str) -> int:
    """从有效会话获取身份，伪造、过期、退出或停用用户的会话全部拒绝。"""
    if not 1 <= len(token) <= 128:
        raise AuthenticationError("会话无效")
    user = connection.execute(
        text(
            "SELECT s.user_id FROM auth_sessions s JOIN access_users u ON u.id=s.user_id "
            "WHERE s.token_hash=:token AND s.expires_at>:now AND u.active=1"
        ),
        {"token": hashlib.sha256(token.encode()).hexdigest(), "now": utc_now()},
    ).scalar()
    if user is None:
        raise AuthenticationError("会话无效")
    return int(user)


def logout(connection: Connection, token: str) -> None:
    """幂等删除当前会话；调用方提交后后续请求不能再使用该令牌。"""
    connection.execute(
        text("DELETE FROM auth_sessions WHERE token_hash=:token"),
        {"token": hashlib.sha256(token.encode()).hexdigest()},
    )
