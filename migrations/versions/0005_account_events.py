"""0005：记录本地账号初始化和密码重置事件。

事件仅保存动作、目标用户、操作来源和 UTC 时间，不保存密码、哈希或令牌。
当前是开发阶段操作记录，数据库最小审计权限及不可篡改治理仍需后续完善。
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建与用户外键关联的账号操作事件表。"""
    op.create_table(
        "auth_account_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("access_users.id"), nullable=False
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    """删除操作记录；真实使用前回退须先导出并保存记录。"""
    op.drop_table("auth_account_events")
