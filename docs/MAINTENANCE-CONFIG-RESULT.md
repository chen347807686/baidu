# 规范收敛第二步：本地维护配置统一入口

2026-11-10。本步不新增业务能力，不改密码算法、权限规则、账号内容或数据库结构。

## 完成内容

新增 dashboard/local_config.py：统一加载非敏感本机设置及受限凭据文件，将维护连接转换为 DatabaseSettings，由既有 make_engine 统一构造连接。manage.py 不再直接解析 JSON、拼接 URL 或 create_engine；先验证配置，再询问两次隐藏密码。

LocalCredentials 三个密码字段使用 SecretStr，严格拒绝空值、错误类型、重复 JSON 键或未知字段；文件限 64 KiB，兼容 UTF-8 BOM，错误不回显内容。路径必须是绝对路径。该入口只读取，不创建或修改凭据文件。

本机工具、测试夹具的目标实例和导入命令的本地边界判断共同读取 LocalMySQLSettings，避免端口分散维护。MySQL 生命周期脚本仍使用 PyMySQL 驱动执行启动就绪探测/关闭，这是工具职责，不是另一套应用 SQLAlchemy 连接工厂。测试管理连接仍使用随机库的独立管理员环境，不读取运行账户数据。

## 配置职责

- ONETITLE_LOCAL_MYSQL_HOST：只接受 127.0.0.1。
- ONETITLE_LOCAL_MYSQL_PORT：默认 13306，范围 1024–65535。
- ONETITLE_LOCAL_MYSQL_DATABASE：目前只接受独立库 onetitle_v2_local。
- ONETITLE_LOCAL_MYSQL_CREDENTIALS_PATH：默认工程 .runtime/mysql/local.json，可显式选择其它受限绝对路径。

维护账号当前仍为本地 root；“统一配置”不代表已经完成最小维护账号权限治理。应用 ONETITLE_DB_* 与维护配置刻意隔离，前者不能把管理员命令重定向到其它数据库。导入 CLI 继续使用应用连接，并与本机设置比较目标边界。

端口变更前应先用旧设置停止本机实例，再更新配置并启动；不会自动搬迁/重启实例。运行应用的 ONETITLE_DB_PORT 仍需显式与新端口一致，本机设置不会静默覆盖应用连接。此能力不用于远端或生产部署。

## 验证

新增纯测试验证维护目标不受应用环境影响、端口覆盖与边界同步、SecretStr 展示、文件格式/重复键/空值/非法字段、无效主机/端口/库名/路径，以及配置失败不询问密码。新增隔离 MySQL CLI 测试确认命令通过统一工厂创建管理员，随后可登录并有维护事件记录。

实际本机 local_mysql.py check 成功读取已有受限文件并查询 MySQL 8.4.13。没有运行真实账号 bootstrap/reset，既有管理员和会话未改变。既有本机凭据文件格式未改，无凭据写入 Git。

最终全套 49 passed；ruff check .、ruff format --check .（68 个文件）、mypy strict（43 个源文件）通过。

类型检查曾发生一次增量缓存内部错误；--no-incremental 完整检查通过，随后普通增量检查恢复通过，未忽略类型错误或改用宽松配置。TestClient 弃用提示仍按原记录保留。

## 边界与后续

本步不修改 main/create_app 重复加载应用配置、Cookie 常量归属、权限最小查询 API 或 HTTP 响应模型，这些分小步处理。下一步提供权限 ID 查询的最小入口，减少报表对权限内部对象的依赖。

历史迁移无改动，无数据库回退需求；撤销代码可恢复原工具配置路径，凭据文件与运行数据不需要恢复。页面无变化。
