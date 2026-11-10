# S0-05：本地浏览器认证 HTTP 接口

2026-11-10。默认仍可独立启动健康服务；显式开启认证时需要 MySQL 配置。

## 接口与防护

- POST /api/v1/auth/login：JSON username/password。成功只返回 status=ok，通过 HttpOnly、SameSite=Strict 的 onetitle_v2_session Cookie 保存会话；再次登录撤销该浏览器旧会话。
- POST /api/v1/auth/logout：幂等撤销服务器会话并删除 Cookie，返回 204。
- GET /api/v1/auth/me：从 Cookie 认证后返回内部 user_id 和当前可见账户，客户端不能通过参数指定身份。每请求新事务，数据库状态不缓存在会话中。
- 所有认证响应禁止缓存；参数校验错误返回通用 422，不回显输入。未登录 401、来源错误 403、超限 429。
- 两个 POST 必须同时带 Origin（http://127.0.0.1:端口 或 http://localhost:端口）和 X-Onetitle-Request: 1；未开放 CORS。参考 https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html 的同源校验及自定义头方案。
- 限速按直连客户端地址每分钟固定窗口十次尝试，成功也计数，数据库原子累计并独立提交。应用重启不会清空当前窗口；窗口切换后可继续。当前仅为单机开发默认，公网策略需另行评估；固定窗口边界附近可出现短时双倍突发。
- 本地 Uvicorn 禁用代理头，不接受伪造 X-Forwarded-For 改变限速来源。数据库记录地址与时间组合摘要，旧限速桶在后续登录时清理。

## 本机启动

先运行 scripts/local_mysql.py migrate，再设置 ONETITLE_ENVIRONMENT=local、ONETITLE_AUTH_ENABLED=true 和 ONETITLE_DB_HOST/PORT/NAME/USER/PASSWORD。数据库使用应用账号 v2_app，不用 root 或迁移账号。密码由受限运行配置注入，不放命令行参数或提交 Git。当前随机本地凭据仍位于受限 .runtime/mysql/local.json。

设置 PYTHONPATH=src 后运行 python -m dashboard。地址默认 http://127.0.0.1:8082。登录 API 可在后续同源页面中调用；Swagger 调试未自动提供所需来源及请求头。ONETITLE_AUTH_ENABLED=false 时不挂载认证接口。

## 实际验证

12 项测试通过，涵盖既有配置/权限/会话核心及新增 HTTP 集成。新增测试使用真实 MySQL、应用数据库账号和独立请求事务：Cookie 属性、来源缺失/伪造、密码不在 422 中回显、登录/退出、退出后旧令牌失效、外部连接提交撤权/停用后下一请求生效、失败尝试不能回滚计数、多应用实例共享限速。Ruff lint、格式与 mypy strict 通过。

合成数据按固定测试 ID 清理，无真实业务账号。TestClient 的现有 httpx 弃用提示仍在。

## 尚未交付

当前没有登录页面、创建账号工具、密码重置、认证审计或权限管理界面；测试合成账号不会保留。Cookie 的 Secure=false 仅用于当前本机 HTTP，未来 HTTPS 部署必须调整。持久会话清理与改密全会话撤销需后续实现。接口测试不等于生产浏览器或部署验收；未对服务器作变更。

下一步：提供受控的本地初始管理员创建工具及改密/会话撤销，之后接入管理审计和登录页面。

0004 回退只删除限速状态；业务和会话结构保留。生产迁移/回退需单独备份验证，本步未执行破坏性回退。
