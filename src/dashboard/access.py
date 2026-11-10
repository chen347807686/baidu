"""账户可见范围的统一 SQL 策略入口。

用户角色、部门、账户归属与额外授权从数据库查询，不在接口中复制规则。
竞价员看本人账户；主管看本部门及额外授权部门；管理员看所有账户。
额外账户授权只扩大已分配账户的范围，未分配账户始终仅管理员可见。
本模块不负责登录：user_id 必须来自服务端认证结果，不能来自请求筛选参数。
每次构建查询均使用数据库当前状态，不缓存权限；调用方需按请求开启新事务，
避免 MySQL 长事务旧快照延迟撤权。写操作权限与审批不由此读取策略授权。
"""

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.sql import Select

from dashboard.schema import account_grants as _account_grants
from dashboard.schema import accounts as _accounts
from dashboard.schema import department_grants as _department_grants
from dashboard.schema import users as _users


def _visible_accounts_query(user_id: int) -> Select[int, str]:
    """返回可组合的账户查询，让报表在聚合与分页之前应用权限过滤。

    不存在、停用或未知角色的用户不返回任何账户。部门授权只对主管生效，
    避免普通竞价员因残留的历史部门授权而获得部门范围。
    """
    # Python 注解不执行运行时校验；必须在构造 SQL 前拒绝隐式数字转换。
    if type(user_id) is not int:
        raise TypeError("身份 ID 必须为整数")
    if not -(2**31) <= user_id <= 2**31 - 1:
        raise ValueError("身份 ID 超出数据库整数范围")
    extra_account = exists().where(
        _account_grants.c.user_id == _users.c.id,
        _account_grants.c.account_id == _accounts.c.id,
    )
    extra_department = exists().where(
        _department_grants.c.user_id == _users.c.id,
        _department_grants.c.department_id == _accounts.c.department_id,
    )
    manager_scope = and_(
        _users.c.role == "manager",
        or_(_accounts.c.department_id == _users.c.department_id, extra_department),
    )
    # 归属以账户的显式字段为准；调整用户所在部门不会自动重写账户归属。
    assigned_scope = and_(
        _accounts.c.owner_id.is_not(None),
        or_(
            and_(_users.c.role == "bidder", _accounts.c.owner_id == _users.c.id),
            manager_scope,
            extra_account,
        ),
    )
    return (
        select(_accounts.c.id, _accounts.c.name)
        .select_from(_accounts.join(_users, _users.c.id == user_id))
        .where(
            _users.c.active.is_(True),
            _users.c.role.in_(["bidder", "manager", "admin"]),
            or_(_users.c.role == "admin", assigned_scope),
        )
        .order_by(_accounts.c.id)
    )


def visible_account_ids(user_id: int) -> Select[int]:
    """单列账户 ID 查询，供 IN 子查询、权限过滤使用，不返回账户名称。

    只接受数据库有符号 INT 范围内的原生 int，拒绝 bool、字符串和浮点数。
    负整数保留数据库可表达的语义（用于隔离合成测试），不存在的用户仍无权限。
    """
    return (
        _visible_accounts_query(user_id)
        .with_only_columns(_accounts.c.id)
        .order_by(None)
    )


def visible_accounts(user_id: int) -> Select[int, str]:
    """双列 (id, name) 查询，仅用于可见账户列表，不能直接用作单列 IN。

    与 ID 入口复用同一权限策略；保留原列表接口及按 ID 排序的行为。
    """
    return _visible_accounts_query(user_id)
