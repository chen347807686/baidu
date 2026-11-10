"""0008：保存本地导入计划指纹、确认状态与结果。

不保存文件原文、账户名称、金额原值或凭据。计划与写入结果在正式入库事务中
一起更新，重复执行已成功计划不再次写入。降级会删除追溯信息，须先归档。
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建带时限和确认指纹的计划表，不生成默认计划。"""
    op.create_table(
        "keyword_import_plans",
        sa.Column("id", sa.String(32, collation="ascii_bin"), primary_key=True),
        *[
            sa.Column(name, sa.String(64, collation="ascii_bin"), nullable=False)
            for name in (
                "input_hash",
                "mapping_hash",
                "rows_hash",
                "state_hash",
                "confirmation",
                "amount_reference_hash",
            )
        ],
        sa.Column("status", sa.String(16, collation="ascii_bin"), nullable=False),
        sa.Column("processed", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("applied_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("applied_at", sa.DateTime),
    )


def downgrade() -> None:
    """删除计划追溯记录，不回滚已经导入的事实数据。"""
    op.drop_table("keyword_import_plans")
