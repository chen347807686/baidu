"""0003：创建登录凭据和服务端会话表。

凭据独立关联现有用户，不为历史用户设置默认密码。会话只保存随机令牌的
SHA-256 摘要，过期时间使用 UTC。降级将丢弃全部登录和会话数据，需备份。
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建一用户一凭据及可撤销的持久会话结构。"""
    op.create_table(
        "auth_credentials",
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("access_users.id"), primary_key=True
        ),
        sa.Column(
            "login",
            sa.String(100, collation="utf8mb4_0900_as_cs"),
            nullable=False,
            unique=True,
        ),
        sa.Column("password_hash", sa.String(256), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64, collation="ascii_bin"), primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("access_users.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expiry", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    """删除登录结构，所有现有会话同时失效。"""
    op.drop_table("auth_sessions")
    op.drop_table("auth_credentials")
