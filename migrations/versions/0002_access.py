"""0002：建立可配置的用户角色、部门、账户归属及额外授权结构。

只创建权限基础表，不创建默认管理员或导入真实账户。角色由数据库约束限定，
关系通过外键保证有效，授权通过联合主键防止重复。删除被引用的归属时默认
拒绝操作，必须先显式调整分配。降级会删除权限数据，执行前必须备份；本地
仅在无业务数据的测试库验证升级，不把 MySQL DDL 视作可自动原子回滚。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按依赖顺序创建部门、用户、账户和授权表。"""
    op.create_table(
        "access_departments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_table(
        "access_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("department_id", sa.Integer, sa.ForeignKey("access_departments.id")),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.CheckConstraint(
            "role IN ('bidder','manager','admin')", name="ck_access_role"
        ),
    )
    op.create_table(
        "access_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("access_users.id")),
        sa.Column("department_id", sa.Integer, sa.ForeignKey("access_departments.id")),
    )
    op.create_table(
        "access_account_grants",
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("access_users.id"), primary_key=True
        ),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("access_accounts.id"),
            primary_key=True,
        ),
    )
    op.create_table(
        "access_department_grants",
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("access_users.id"), primary_key=True
        ),
        sa.Column(
            "department_id",
            sa.Integer,
            sa.ForeignKey("access_departments.id"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    """逆依赖删除权限表；此操作会丢弃授权记录，不适用于无备份业务库。"""
    for table in (
        "access_department_grants",
        "access_account_grants",
        "access_accounts",
        "access_users",
        "access_departments",
    ):
        op.drop_table(table)
