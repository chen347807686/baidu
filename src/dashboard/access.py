"""账户可见范围的统一 SQL 策略入口。

用户角色、部门、账户归属与额外授权从数据库查询，不在接口中复制规则。
竞价员看本人账户；主管看本部门及额外授权部门；管理员看所有账户。
额外账户授权只扩大已分配账户的范围，未分配账户始终仅管理员可见。
本模块不负责登录：user_id 必须来自服务端认证结果，不能来自请求筛选参数。
每次构建查询均使用数据库当前状态，不缓存权限；调用方需按请求开启新事务，
避免 MySQL 长事务旧快照延迟撤权。写操作权限与审批不由此读取策略授权。
"""

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    exists,
    or_,
    select,
)
from sqlalchemy.sql import Select

# 这里只描述查询所需结构，绝不调用 create_all；实际结构由版本迁移创建。
metadata = MetaData()
users = Table(
    "access_users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100)),
    Column("role", String(20)),
    Column("department_id", Integer),
    Column("active", Boolean),
)
accounts = Table(
    "access_accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100)),
    Column("owner_id", Integer),
    Column("department_id", Integer),
)
account_grants = Table(
    "access_account_grants",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("account_id", Integer, primary_key=True),
)
department_grants = Table(
    "access_department_grants",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("department_id", Integer, primary_key=True),
)


def visible_accounts(user_id: int) -> Select[int, str]:
    """返回可组合的账户查询，让报表在聚合与分页之前应用权限过滤。

    不存在、停用或未知角色的用户不返回任何账户。部门授权只对主管生效，
    避免普通竞价员因残留的历史部门授权而获得部门范围。
    """
    extra_account = exists().where(
        account_grants.c.user_id == users.c.id,
        account_grants.c.account_id == accounts.c.id,
    )
    extra_department = exists().where(
        department_grants.c.user_id == users.c.id,
        department_grants.c.department_id == accounts.c.department_id,
    )
    manager_scope = and_(
        users.c.role == "manager",
        or_(accounts.c.department_id == users.c.department_id, extra_department),
    )
    # 归属以账户的显式字段为准；调整用户所在部门不会自动重写账户归属。
    assigned_scope = and_(
        accounts.c.owner_id.is_not(None),
        or_(
            and_(users.c.role == "bidder", accounts.c.owner_id == users.c.id),
            manager_scope,
            extra_account,
        ),
    )
    return (
        select(accounts.c.id, accounts.c.name)
        .select_from(accounts.join(users, users.c.id == user_id))
        .where(
            users.c.active.is_(True),
            users.c.role.in_(["bidder", "manager", "admin"]),
            or_(users.c.role == "admin", assigned_scope),
        )
        .order_by(accounts.c.id)
    )
