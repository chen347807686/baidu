"""0007：建立独立正式关键词日报结构，不导入任何旧数据。

唯一键沿用已核实的日期/账户/计划/单元/关键词ID/设备粒度，指标使用非负
整数和 DECIMAL 金额。词与设备采用二进制排序避免字面主键被合并。
此表与合成报表表分离；降级会丢失正式日报，必须先备份和确认恢复方案。
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None
COUNTERS = (
    "impression",
    "click",
    "conv_form",
    "conv_3sent",
    "conv_lead",
    "conv_1sent",
    "conv_dial",
    "conv_wechat",
    "conv_78",
    "conv_92",
)


def upgrade() -> None:
    """创建 InnoDB 日报表与账户日期索引，不执行种子或数据回填。"""
    op.create_table(
        "keyword_daily_v2",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("access_accounts.id"),
            primary_key=True,
        ),
        sa.Column("campaign_id", sa.BigInteger, primary_key=True),
        sa.Column("adgroup_id", sa.BigInteger, primary_key=True),
        sa.Column("w_info_id", sa.BigInteger, primary_key=True),
        sa.Column(
            "device", sa.String(255, collation="utf8mb4_0900_bin"), primary_key=True
        ),
        sa.Column(
            "user_name", sa.String(255, collation="utf8mb4_0900_bin"), nullable=False
        ),
        sa.Column("campaign_name", sa.Text, nullable=False),
        sa.Column("adgroup_name", sa.Text, nullable=False),
        sa.Column("keyword", sa.Text(collation="utf8mb4_0900_bin"), nullable=False),
        sa.Column("cost", sa.Numeric(18, 4), nullable=False),
        *[sa.Column(name, sa.BigInteger, nullable=False) for name in COUNTERS],
        sa.CheckConstraint(
            "cost >= 0 AND campaign_id >= 0 AND adgroup_id >= 0 AND w_info_id >= 0",
            name="ck_keyword_identity_amount",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in COUNTERS), name="ck_keyword_counts"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_keyword_daily_account_date", "keyword_daily_v2", ["account_id", "date"]
    )


def downgrade() -> None:
    """删除正式事实表；上线后禁止把此降级当作无损代码回退。"""
    op.drop_table("keyword_daily_v2")
