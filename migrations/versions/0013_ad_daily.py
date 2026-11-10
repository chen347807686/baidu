"""0013：建立独立统一投放日事实，保留现有关键词表与接口。

按账户、北京日期、报表类型与完整维度指纹存储，未知指标可空。组合外键
确保来源映射属于对应内部账户；只建空表，不计算指纹、不导入或转换旧数据。
降级需停写并备份，非空事实表拒绝自动删除。历史迁移不引用运行时模型。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加组合来源绑定键，建立投放事实与限定查询索引。"""
    op.create_unique_constraint(
        "uq_account_source_binding", "account_sources", ["id", "account_id"]
    )
    op.create_table(
        "ad_daily",
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("access_accounts.id"),
            primary_key=True,
        ),
        sa.Column("stat_date", sa.Date, primary_key=True),
        sa.Column(
            "report_kind", sa.String(32, collation="utf8mb4_0900_bin"), primary_key=True
        ),
        sa.Column("grain_key", sa.BINARY(32), primary_key=True),
        sa.Column("source_account_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grain_version", mysql.SMALLINT(unsigned=True), nullable=False),
        *[
            sa.Column(name, mysql.BIGINT(unsigned=True))
            for name in (
                "project_id",
                "campaign_id",
                "adgroup_id",
                "keyword_id",
                "creative_id",
            )
        ],
        *[
            sa.Column(name, sa.Text(collation="utf8mb4_0900_bin"))
            for name in ("keyword_text", "search_term", "entity_name")
        ],
        sa.Column("device", sa.String(32, collation="utf8mb4_0900_bin")),
        sa.Column(
            "device_scope", sa.String(32, collation="utf8mb4_0900_bin"), nullable=False
        ),
        sa.Column("dimensions", sa.JSON, nullable=False),
        sa.Column("impressions", mysql.BIGINT(unsigned=True)),
        sa.Column("clicks", mysql.BIGINT(unsigned=True)),
        sa.Column("cost", sa.Numeric(18, 4)),
        sa.Column(
            "source_revision",
            sa.String(64, collation="utf8mb4_0900_bin"),
            nullable=False,
        ),
        sa.Column("source_checked_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("content_hash", sa.BINARY(32), nullable=False),
        sa.Column("ingested_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_account_id", "account_id"],
            ["account_sources.id", "account_sources.account_id"],
            name="fk_ad_daily_source_binding",
        ),
        sa.CheckConstraint(
            "report_kind IN ('keyword','search_term','creative')",
            name="ck_ad_daily_kind",
        ),
        sa.CheckConstraint(
            "device_scope IN ('detail','summary','unknown')",
            name="ck_ad_daily_device_scope",
        ),
        sa.CheckConstraint("grain_version >= 1", name="ck_ad_daily_grain_version"),
        sa.CheckConstraint("cost >= 0", name="ck_ad_daily_cost"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_cs",
    )
    op.create_index(
        "ix_ad_daily_account_kind_date",
        "ad_daily",
        ["account_id", "report_kind", "stat_date"],
    )
    op.create_index(
        "ix_ad_daily_account_keyword_date",
        "ad_daily",
        ["account_id", "keyword_id", "stat_date"],
    )


def downgrade() -> None:
    """仅空事实表可回退，不自动丢弃业务数据或映射。"""
    if op.get_bind().execute(sa.text("SELECT 1 FROM ad_daily LIMIT 1")).first():
        raise RuntimeError("投放日报非空，拒绝自动降级；请先停写备份并核对")
    op.drop_table("ad_daily")
    op.drop_constraint("uq_account_source_binding", "account_sources", type_="unique")
