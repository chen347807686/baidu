"""0012：记录本地来源账户维护事件，不存业务值或秘密。

事件与映射变更同事务提交；本步只是开发维护留痕，尚非生产不可篡改审计。
降级仅允许空事件表，非空时拒绝自动丢弃记录，需停写并备份后人工处理。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建立最小事件表，版本唯一键防止同版本重复记录。"""
    op.create_table(
        "account_source_events",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "source_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("account_sources.id"),
            nullable=False,
        ),
        sa.Column(
            "action", sa.String(16, collation="utf8mb4_0900_bin"), nullable=False
        ),
        sa.Column("mapping_version", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column("changed_fields", sa.JSON, nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "action IN ('create','update')", name="ck_source_event_action"
        ),
        sa.UniqueConstraint(
            "source_id", "mapping_version", name="uq_source_event_version"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_cs",
    )


def downgrade() -> None:
    """有记录则拒绝自动删除；维护回退前必须停止写入并核对备份。"""
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM account_source_events LIMIT 1"))
        .first()
    ):
        raise RuntimeError("存在来源维护事件，拒绝自动降级")
    op.drop_table("account_source_events")
