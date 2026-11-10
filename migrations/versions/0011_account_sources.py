"""0011：新增外部来源账户与内部权限账户之间的独立映射。

保持来源稳定 ID 和命名空间唯一，不按账户名称合并；仅存凭据引用，不导入
真实账户或密钥。此冻结迁移独立声明结构，由漂移测试对照运行时模型。
降级仅允许空表，已有映射需先备份并通过后续受控流程处理，不自动删除。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建空映射表、唯一身份和账户外键，不改现有权限及日报数据。"""
    op.create_table(
        "account_sources",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("access_accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "source_namespace",
            sa.String(64, collation="utf8mb4_0900_bin"),
            nullable=False,
        ),
        sa.Column(
            "external_account_id",
            sa.String(128, collation="utf8mb4_0900_bin"),
            nullable=False,
        ),
        sa.Column("source_account_name", sa.String(255), nullable=False),
        sa.Column(
            "channel", sa.String(32, collation="utf8mb4_0900_bin"), nullable=False
        ),
        sa.Column("credential_ref", sa.String(128)),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("mapping_version", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "source_namespace", "external_account_id", name="uq_account_source_identity"
        ),
        sa.CheckConstraint(
            "channel = 'paid_baidu_search'", name="ck_account_source_channel"
        ),
        sa.CheckConstraint("mapping_version >= 1", name="ck_account_source_version"),
        sa.CheckConstraint(
            "CHAR_LENGTH(source_namespace) >= 1 AND CHAR_LENGTH(external_account_id) >= 1",
            name="ck_account_source_identity",
        ),
        sa.CheckConstraint("active IN (0,1)", name="ck_account_source_active"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_cs",
    )
    op.create_index(
        "ix_account_sources_account_active", "account_sources", ["account_id", "active"]
    )


def downgrade() -> None:
    """有映射则阻断降级，防止回退代码时静默丢失来源身份。"""
    if (
        op.get_bind().execute(sa.text("SELECT 1 FROM account_sources LIMIT 1")).first()
        is not None
    ):
        raise RuntimeError("来源账户映射非空，拒绝自动降级；请先备份并核对")
    op.drop_table("account_sources")
