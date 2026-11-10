# S0-02 本机 MySQL 与迁移边界

2026-11-10 用户确定使用 MySQL，先本机跑通、后部署；允许在 V2 目录准备独立本机实例。本次只完成数据基础，不表示业务系统已完成。

## 实现与验证

- 官方 MySQL 8.4.13 Windows ZIP，校验 MD5 与官方下载页一致：7b94489d5524dea9b27097068bea25a8。
- 二进制、数据、日志、随机生成的本机凭据均位于忽略的 `.runtime/mysql`；目录 ACL 限制到当前用户与 SYSTEM。没有使用服务器或旧系统凭据。
- 127.0.0.1:13306；独立库 onetitle_v2_local；utf8mb4_0900_as_cs。此排序规则仅作当前基础，业务词匹配语义仍需逐项核对。
- 迁移账号仅有本库权限；应用账号仅 SELECT/INSERT/UPDATE/DELETE，测试确认不能 CREATE TABLE。
- SQLAlchemy 连接与 Alembic 显式迁移分离。0001 为空结构基线，仅产生 alembic_version，未提前定义业务表。
- 8 项测试通过，含真实 MySQL 重复迁移、连接不建表、权限验证及 S0-01 回归。Ruff、格式、mypy strict 通过。
- 本地 MySQL 停止、重启、版本查询通过；初始认证匹配问题已修正，失败初始化目录保留在 .runtime/mysql/data-first-attempt。

## 使用（项目根目录）

```powershell
uv sync --locked
uv run --locked python scripts/local_mysql.py start
uv run --locked python scripts/local_mysql.py check
uv run --locked python scripts/local_mysql.py migrate
uv run --locked python scripts/local_mysql.py test
uv run --locked python scripts/local_mysql.py stop
```

目前数据库已启动；再次 start 会拒绝占用端口，不结束已有进程。运行期文件不提交 Git。新机器需从官方 https://dev.mysql.com/downloads/mysql/8.4.html?os=3 下载同版本 ZIP，校验后解压到 `.runtime/mysql`，再运行 start；本机已完成此步骤。此工具仅用于本机开发，不是生产部署脚本。

服务启动仍按 README；当前 /health 为进程存活接口，不声明数据库就绪。数据库配置按 ONETITLE_DB_HOST/PORT/NAME/USER/PASSWORD 注入；本地脚本从受限文件注入，不打印凭据。

回退：检出前一代码提交，停止本地实例；保留数据目录与凭据，不删除数据。空基线迁移不涉及业务数据恢复。后续迁移必须单独验证备份恢复。

遗留：TestClient 的 httpx 弃用提示仍在；业务表、登录权限、报表和前端尚未实现。下一步应冻结一个报表及其账户权限、指标和分页契约，再建立最小业务链路；服务器部署后置。
