"""0009：固定角色的二进制排序规则，防止数据库默认规则造成误授权。

先按字节检查已有角色，非规范值仅阻止升级，不自动修正或提升权限。结构变更
在单条 MySQL ALTER 中替换排序规则和 CHECK；本项目本地验证版本为 MySQL 8.4。
降级不能重新引入大小写不敏感角色比较，因此明确拒绝直接撤销此安全修复。
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """有歧义的数据在 DDL 前停止，无歧义时以单条语句更新约束。"""
    invalid: int = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM access_users WHERE CAST(role AS BINARY) NOT IN (BINARY 'bidder',BINARY 'manager',BINARY 'admin')"
            )
        )
        .scalar_one()
    )
    if invalid:
        raise RuntimeError(
            "存在非规范角色，迁移停止；请人工核对后处理，不自动归为管理员"
        )
    op.execute(
        sa.text(
            "ALTER TABLE access_users DROP CHECK ck_access_role, MODIFY COLUMN role VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL, ADD CONSTRAINT ck_access_role CHECK (role IN ('bidder','manager','admin'))"
        )
    )


def downgrade() -> None:
    """禁止恢复有风险的旧排序规则；代码回退与安全结构回退必须分别评估。"""
    raise RuntimeError("禁止直接撤销角色安全约束；请制定兼容的独立迁移方案")
