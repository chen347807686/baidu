"""0006：创建仅用于本地链路验证的合成关键词日报表。

此表不是正式百度日报结构，不承诺真实采集粒度或导入契约。无默认样本，
测试按事务写入并回滚；后续真实模型须独立设计。降级会删除所有合成记录。
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建样本事实表，词文本使用二进制排序规则保持字面分组。"""
    op.create_table(
        "report_sample_daily",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("access_accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "keyword", sa.String(255, collation="utf8mb4_0900_bin"), nullable=False
        ),
        sa.Column("cost", sa.Numeric(18, 4), nullable=False),
        *[
            sa.Column(name, sa.Integer, nullable=False)
            for name in (
                "impression",
                "click",
                "conv_form",
                "conv_lead",
                "conv_dial",
                "conv_wechat",
                "conv_1sent",
                "conv_3sent",
            )
        ],
    )
    op.create_index(
        "ix_report_sample_account_day", "report_sample_daily", ["account_id", "day"]
    )


def downgrade() -> None:
    """仅删除合成样本表，不处理真实报表数据。"""
    op.drop_table("report_sample_daily")
