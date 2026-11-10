"""0001：建立数据库版本迁移的空基线。

当前业务结构尚未冻结，本版本不创建业务表，也不写入种子数据。
Alembic 自行维护版本记录；升级和降级函数均为空，不能据此推断后续
含业务数据的迁移也可无损回退。
"""

# 首个版本没有父版本；这些标识由 Alembic 用于解析迁移顺序。
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 升级到空基线，仅由 Alembic 更新版本记录。
    pass


def downgrade() -> None:
    # 撤销空基线，不删除任何业务结构或业务数据。
    pass
