"""0004：为登录接口创建跨进程共享的固定窗口限速记录。

仅保存来源地址与时间窗口的摘要及计数，不保存密码、登录名或原始地址。
降级会清空限速状态；登录会话和用户数据不受影响。
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建限速桶，主键保证同一窗口的并发更新由数据库串行处理。"""
    op.create_table(
        "auth_login_limits",
        sa.Column("bucket", sa.String(64, collation="ascii_bin"), primary_key=True),
        sa.Column("window", sa.BigInteger, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
    )
    op.create_index("ix_auth_limits_window", "auth_login_limits", ["window"])


def downgrade() -> None:
    """删除限速记录，下次升级后从空计数开始。"""
    op.drop_table("auth_login_limits")
