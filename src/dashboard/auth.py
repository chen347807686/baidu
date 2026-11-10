"""本地密码认证及持久会话的服务层。

密码使用随机盐与 PBKDF2-HMAC-SHA256 哈希；会话使用高熵随机令牌，仅摘要
进入数据库。每次认证会话重新检查用户启用状态，不保存角色或账户权限快照。
本模块不处理 HTTP、Cookie、限速或账号管理授权；调用方负责提交事务，
并将认证得到的用户 ID 传给统一权限策略。当前仅用于本地验证。
"""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

ITERATIONS = 600_000
# 旧参数可验证，新密码不低于当前安全基线；固定预算限制异常记录的资源消耗。
MIN_LEGACY_ITERATIONS = 100_000
VERIFICATION_BUDGET = 2_000_000


class AuthenticationError(ValueError):
    """统一认证失败，不向调用方区分用户不存在、停用或密码错误。"""


def _current_rounds() -> int:
    """拒绝不受支持的新策略，避免参数变更后静默走不完整的验证路径。"""
    if type(ITERATIONS) is not int or not 600_000 <= ITERATIONS <= VERIFICATION_BUDGET:
        raise ValueError("密码策略参数无效")
    return ITERATIONS


def _parse_hash(encoded: str | None) -> tuple[int, bytes, bytes] | None:
    """只解析受支持且有边界的历史记录，不执行任意算法或超大迭代数。"""
    if not isinstance(encoded, str) or len(encoded) > 256:
        return None
    parts = encoded.split("$")
    if len(parts) != 4:
        return None
    algorithm, rounds_text, salt, expected = parts
    if algorithm != "pbkdf2_sha256" or not re.fullmatch(
        r"[1-9][0-9]{0,6}", rounds_text
    ):
        return None
    rounds = int(rounds_text)
    if not MIN_LEGACY_ITERATIONS <= rounds <= VERIFICATION_BUDGET:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{32}", salt) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected
    ):
        return None
    return rounds, bytes.fromhex(salt), bytes.fromhex(expected)


def _derive(password: bytes, salt: bytes, rounds: int) -> bytes:
    """固定算法的计算入口，测试可观察预算而不替换全局 hashlib。"""
    return hashlib.pbkdf2_hmac("sha256", password, salt, rounds)


def hash_password(password: str) -> str:
    """新密码使用当前参数和随机盐，不改变既有哈希格式。"""
    rounds = _current_rounds()
    if not 12 <= len(password) <= 128:
        raise ValueError("密码长度必须为 12 至 128 个字符")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("密码文本编码无效") from None
    salt = secrets.token_bytes(16)
    digest = _derive(encoded, salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    """按记录参数验证，并补齐固定 PBKDF2 总预算；不承诺端到端恒定耗时。

    缺失/损坏/不支持的记录仍执行同样总迭代数；超长或无法编码的密码用占位
    字节计算后拒绝。补算不影响原摘要，原参数更高的有效哈希也能继续验证。
    """
    current = _current_rounds()
    parsed = _parse_hash(encoded)
    valid_input = type(password) is str and len(password) <= 128
    try:
        password_bytes = (
            password.encode("utf-8") if valid_input else b"invalid-password"
        )
    except UnicodeEncodeError:
        valid_input = False
        password_bytes = b"invalid-password"
    rounds, salt, expected = (
        parsed if parsed is not None else (current, bytes(16), bytes(32))
    )
    actual = _derive(password_bytes, salt, rounds)
    if rounds < VERIFICATION_BUDGET:
        # 独立补算到统一预算，不修改校验用的原始摘要；不能省略失败路径的计算。
        _derive(b"authentication-padding", bytes(16), VERIFICATION_BUDGET - rounds)
    matches = hmac.compare_digest(actual, expected)
    return parsed is not None and valid_input and matches


def needs_rehash(encoded: str) -> bool:
    """只升级较弱的已支持参数，不把更强历史哈希降级。"""
    current = _current_rounds()
    parsed = _parse_hash(encoded)
    return parsed is not None and parsed[0] < current


def utc_now() -> datetime:
    """返回 UTC 无时区对象，与 MySQL DATETIME 的存储约定一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


def login(connection: Connection, username: str, password: str) -> str:
    """验证凭据并建立八小时会话，原始令牌仅返回给调用方一次。"""
    row = (
        connection.execute(
            text(
                "SELECT c.user_id, c.password_hash, u.active FROM auth_credentials c "
                "JOIN access_users u ON u.id=c.user_id WHERE c.login=:login FOR UPDATE"
            ),
            {"login": username},
        )
        .mappings()
        .first()
    )
    valid = verify_password(password, str(row["password_hash"]) if row else None)
    if row is None or not valid or not row["active"]:
        raise AuthenticationError("登录失败")
    if needs_rehash(str(row["password_hash"])):
        # 登录已持有凭据行锁，参数升级与会话创建同事务；不改变密码或撤销旧会话。
        connection.execute(
            text("UPDATE auth_credentials SET password_hash=:hash WHERE user_id=:user"),
            {"hash": hash_password(password), "user": row["user_id"]},
        )
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
