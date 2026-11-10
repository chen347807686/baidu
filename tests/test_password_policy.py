"""密码参数兼容与失败计算预算验证。

纯测试通过模块内计算入口观察迭代预算，不修改全局 hashlib；真实 MySQL
测试验证成功登录升级、失败不更新、事务回滚、较强哈希不降级及旧会话保留。
不读取真实管理员密码，不把合成密码或哈希写入日志。
"""

import hashlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dashboard import auth
from dashboard.database import load_database_settings, make_engine


def encoded_hash(rounds: int) -> str:
    """生成已知参数的合成历史格式，不使用当前新密码策略。"""
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac("sha256", b"synthetic-legacy-password", salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


@pytest.mark.parametrize(
    "record",
    [
        None,
        "broken",
        "other$600000$" + "00" * 16 + "$" + "11" * 32,
        "pbkdf2_sha256$9999999$" + "00" * 16 + "$" + "11" * 32,
        "pbkdf2_sha256$600000$bad$bad",
        "pbkdf2_sha256$100000$" + "00" * 16 + "$" + "11" * 32,
        "pbkdf2_sha256$2000000$" + "00" * 16 + "$" + "11" * 32,
    ],
)
def test_failure_work_budget(
    monkeypatch: pytest.MonkeyPatch, record: str | None
) -> None:
    """参数变更后，缺失/损坏/旧记录都执行有界且总数相同的迭代。"""
    calls: list[int] = []

    def observe(password: bytes, salt: bytes, rounds: int) -> bytes:
        """只测试控制流与预算，密码正确性另用真实算法验证。"""
        calls.append(rounds)
        return bytes(32)

    monkeypatch.setattr(auth, "_derive", observe)
    monkeypatch.setattr(auth, "ITERATIONS", 700000)
    assert auth.verify_password("wrong", record) is False
    assert sum(calls) == auth.VERIFICATION_BUDGET and all(
        0 < value <= auth.VERIFICATION_BUDGET for value in calls
    )
    calls.clear()
    assert not auth.verify_password("x" * 129, record)
    assert sum(calls) == auth.VERIFICATION_BUDGET


def test_real_legacy_and_stronger_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """真正用历史轮数验证，提升策略后旧记录不失效、更强记录不降级。"""
    old, strong = encoded_hash(100000), encoded_hash(800000)
    monkeypatch.setattr(auth, "ITERATIONS", 700000)
    assert auth.verify_password("synthetic-legacy-password", old)
    assert auth.needs_rehash(old)
    assert auth.verify_password("synthetic-legacy-password", strong)
    assert not auth.needs_rehash(strong)
    with pytest.raises(ValueError):
        monkeypatch.setattr(auth, "ITERATIONS", auth.VERIFICATION_BUDGET + 1)
        auth.hash_password("synthetic-legacy-password")


@pytest.mark.mysql
@pytest.mark.skipif(
    not os.environ.get("ONETITLE_TEST_MYSQL_PASSWORD"), reason="需要隔离 MySQL"
)
def test_login_rehash_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    """只有有效启用账号成功登录才升级，回滚时旧哈希和旧会话都保留。"""
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    engine = make_engine(load_database_settings())
    old = encoded_hash(600000)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(
                    text(
                        "INSERT INTO access_users(id,name,role,active) VALUES(-70,'参数升级样本','bidder',1)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO auth_credentials(user_id,login,password_hash) VALUES(-70,'rehash-test',:hash)"
                    ),
                    {"hash": old},
                )
                old_session = auth.login(
                    conn, "rehash-test", "synthetic-legacy-password"
                )
                monkeypatch.setattr(auth, "ITERATIONS", 700000)
                original_derive = auth._derive
                observed_rounds: list[int] = []

                def track_derive(
                    password_bytes: bytes, salt: bytes, rounds: int
                ) -> bytes:
                    """观察真正登录失败路径，不只测试验证函数本身。"""
                    observed_rounds.append(rounds)
                    return original_derive(password_bytes, salt, rounds)

                monkeypatch.setattr(auth, "_derive", track_derive)
                for username, password in [
                    ("rehash-test", "wrong"),
                    ("missing", "synthetic-legacy-password"),
                ]:
                    observed_rounds.clear()
                    with pytest.raises(auth.AuthenticationError):
                        auth.login(conn, username, password)
                    assert sum(observed_rounds) == auth.VERIFICATION_BUDGET
                assert (
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=-70"
                        )
                    ).scalar_one()
                    == old
                )
                savepoint = conn.begin_nested()
                auth.login(conn, "rehash-test", "synthetic-legacy-password")
                savepoint.rollback()
                assert (
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=-70"
                        )
                    ).scalar_one()
                    == old
                )
                assert auth.authenticate(conn, old_session) == -70
                token = auth.login(conn, "rehash-test", "synthetic-legacy-password")
                current = str(
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=-70"
                        )
                    ).scalar_one()
                )
                assert current.startswith("pbkdf2_sha256$700000$") and current != old
                assert (
                    auth.authenticate(conn, token)
                    == auth.authenticate(conn, old_session)
                    == -70
                )
                auth.login(conn, "rehash-test", "synthetic-legacy-password")
                assert (
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=-70"
                        )
                    ).scalar_one()
                    == current
                )
                strong = encoded_hash(800000)
                conn.execute(
                    text(
                        "UPDATE auth_credentials SET password_hash=:hash WHERE user_id=-70"
                    ),
                    {"hash": strong},
                )
                auth.login(conn, "rehash-test", "synthetic-legacy-password")
                assert (
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=-70"
                        )
                    ).scalar_one()
                    == strong
                )
                conn.execute(
                    text(
                        "UPDATE auth_credentials SET password_hash=:hash WHERE user_id=-70"
                    ),
                    {"hash": old},
                )
                conn.execute(text("UPDATE access_users SET active=0 WHERE id=-70"))
                with pytest.raises(auth.AuthenticationError):
                    auth.login(conn, "rehash-test", "synthetic-legacy-password")
                assert (
                    conn.execute(
                        text(
                            "SELECT password_hash FROM auth_credentials WHERE user_id=-70"
                        )
                    ).scalar_one()
                    == old
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
