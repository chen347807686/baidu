"""权限与关键词报表的共享运行时表结构，不执行任何建表或连接操作。

查询、比较与写入层统一依赖这里；历史迁移保持独立冻结，通过测试核对实际
迁移结构。此处描述当前目标结构，安全结构修复由独立迁移实施，导入不执行 DDL。
认证和导入计划目前使用参数化 SQL，本步只收口原先重复声明的七张表。
"""

import sqlalchemy as sa

metadata = sa.MetaData()
KEYWORD_COUNTS = (
    "impression",
    "click",
    "conv_form",
    "conv_3sent",
    "conv_lead",
    "conv_1sent",
    "conv_dial",
    "conv_wechat",
    "conv_78",
    "conv_92",
)

# 合成报表保留已知指标子集，列名从统一计数清单派生。
REPORT_COUNTS = tuple(
    name for name in KEYWORD_COUNTS if name not in {"conv_78", "conv_92"}
)

departments = sa.Table(
    "access_departments",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
)
users = sa.Table(
    "access_users",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("role", sa.String(20, collation="utf8mb4_0900_bin"), nullable=False),
    sa.Column("department_id", sa.Integer, sa.ForeignKey("access_departments.id")),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.CheckConstraint("role IN ('bidder','manager','admin')", name="ck_access_role"),
)
accounts = sa.Table(
    "access_accounts",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("owner_id", sa.Integer, sa.ForeignKey("access_users.id")),
    sa.Column("department_id", sa.Integer, sa.ForeignKey("access_departments.id")),
)
account_grants = sa.Table(
    "access_account_grants",
    metadata,
    sa.Column(
        "user_id", sa.Integer, sa.ForeignKey("access_users.id"), primary_key=True
    ),
    sa.Column(
        "account_id", sa.Integer, sa.ForeignKey("access_accounts.id"), primary_key=True
    ),
)
department_grants = sa.Table(
    "access_department_grants",
    metadata,
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
samples = sa.Table(
    "report_sample_daily",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("day", sa.Date, nullable=False),
    sa.Column(
        "account_id", sa.Integer, sa.ForeignKey("access_accounts.id"), nullable=False
    ),
    sa.Column("keyword", sa.String(255, collation="utf8mb4_0900_bin"), nullable=False),
    sa.Column("cost", sa.Numeric(18, 4), nullable=False),
    *[sa.Column(name, sa.Integer, nullable=False) for name in REPORT_COUNTS],
)
sa.Index("ix_report_sample_account_day", samples.c.account_id, samples.c.day)
keyword_daily = sa.Table(
    "keyword_daily_v2",
    metadata,
    sa.Column("date", sa.Date, primary_key=True),
    sa.Column(
        "account_id", sa.Integer, sa.ForeignKey("access_accounts.id"), primary_key=True
    ),
    sa.Column("campaign_id", sa.BigInteger, primary_key=True),
    sa.Column("adgroup_id", sa.BigInteger, primary_key=True),
    sa.Column("w_info_id", sa.BigInteger, primary_key=True),
    sa.Column("device", sa.String(255, collation="utf8mb4_0900_bin"), primary_key=True),
    sa.Column(
        "user_name", sa.String(255, collation="utf8mb4_0900_bin"), nullable=False
    ),
    sa.Column("campaign_name", sa.Text(collation="utf8mb4_0900_as_cs"), nullable=False),
    sa.Column("adgroup_name", sa.Text(collation="utf8mb4_0900_as_cs"), nullable=False),
    sa.Column("keyword", sa.Text(collation="utf8mb4_0900_bin"), nullable=False),
    sa.Column("cost", sa.Numeric(18, 4), nullable=False),
    *[sa.Column(name, sa.BigInteger, nullable=False) for name in KEYWORD_COUNTS],
    sa.CheckConstraint(
        "cost >= 0 AND campaign_id >= 0 AND adgroup_id >= 0 AND w_info_id >= 0",
        name="ck_keyword_identity_amount",
    ),
    sa.CheckConstraint(
        " AND ".join(f"{name} >= 0" for name in KEYWORD_COUNTS),
        name="ck_keyword_counts",
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_0900_as_cs",
)
sa.Index(
    "ix_keyword_daily_account_date", keyword_daily.c.account_id, keyword_daily.c.date
)
# 列名与键顺序从实际 Table 派生，写入和对比不再手工列第二份。
KEYWORD_FIELDS = tuple(keyword_daily.c.keys())
KEYWORD_KEYS = tuple(column.name for column in keyword_daily.primary_key.columns)
