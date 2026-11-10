"""登录限速时钟的纯回归，不连接真实数据库。

使用合成连接与显式时钟验证窗口、重试秒数和跨分钟行为；不替换全局时间，
不改变真实会话时间或其他测试实例。真实多实例共享计数由 HTTP 集成测试覆盖。
"""

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from dashboard.auth_http import consume_attempt


def test_rate_limit_clock_snapshot() -> None:
    """同次请求只取一次时刻，分钟末尾提示与本次计数窗口保持一致。"""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.scalar_one.return_value = 11
    clock = MagicMock(side_effect=[119.9, 120.0])
    with pytest.raises(HTTPException) as caught:
        consume_attempt(engine, "synthetic-source", clock=clock)
    clock.assert_called_once_with()
    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "1"}
    parameters = conn.execute.call_args_list[1].args[1]
    assert parameters == {
        "window": 1,
        "bucket": hashlib.sha256(b"synthetic-source:1").hexdigest(),
    }
    # 下一分钟使用新窗口；前一分钟超限不影响新窗口的首次尝试。
    conn.execute.return_value.scalar_one.return_value = 1
    consume_attempt(engine, "synthetic-source", clock=lambda: 120.0)
    assert conn.execute.call_args_list[-2].args[1]["window"] == 2
