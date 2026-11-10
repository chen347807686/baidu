"""0010：显式统一正式日报表默认及计划/单元名称排序规则。

只设置表默认和两列，不使用 CONVERT TO 改写全表字符列，保留关键词、设备
和源账户名的二进制规则。没有名称归一化、文本清洗或数据导入。
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """一条 ALTER 明确表默认与名称列，避免依赖字符集的默认规则。"""
    op.execute(
        sa.text(
            "ALTER TABLE keyword_daily_v2 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs, MODIFY COLUMN campaign_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs NOT NULL, MODIFY COLUMN adgroup_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs NOT NULL"
        )
    )


def downgrade() -> None:
    """恢复 0007 的名称比较规则，不改三列二进制规则或角色安全结构。"""
    op.execute(
        sa.text(
            "ALTER TABLE keyword_daily_v2 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci, MODIFY COLUMN campaign_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL, MODIFY COLUMN adgroup_name TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL"
        )
    )
