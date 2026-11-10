"""Alembic 在线迁移执行环境。

由 Alembic 命令加载，使用统一配置建立连接并执行待应用的版本脚本。
当前共享元数据只覆盖部分表，尚未接为自动生成的完整模型集，也未提供离线 SQL
模式。MySQL DDL 可能隐式提交，事务上下文不代表任意结构变更都可原子回滚。
"""

from alembic import context

from dashboard.database import load_database_settings, make_engine


def run() -> None:
    """在独立连接中执行迁移，无论成功失败均释放引擎资源。"""
    engine = make_engine(load_database_settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


# Alembic 加载此文件时即执行；普通应用启动路径不会导入此模块。
run()
