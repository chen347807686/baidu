# OneTitle Dashboard V2

当前已完成 S0 基础、账户权限、认证、报表读链路及部分 S1 数据能力。2026-11-11 起主线聚焦关键词质量、规则否词标注、原词追溯和大模型拓词；目前 K0/K1 已完成，K2 仅完成不改数据库的质量/否词纯函数兼容层与契约样例测试，接口、页面和真实拓词任务尚未实现。现成数据库结构不随本项目自动迁移。

## 当前状态

默认 pytest 会跳过需要显式本机 MySQL 凭据的集成测试；跳过项不代表数据库验收完成。完整主线进度见 `docs/PLAN.md`、需求见 `docs/RPD.md`、判定契约见 `docs/KEYWORD-JUDGMENT-CONTRACT.md`。

## 本地运行（PowerShell）

安装 uv 后，在本目录执行：

```powershell
uv sync --locked
$env:PYTHONPATH='src'
$env:ONETITLE_ENVIRONMENT='local'
uv run --locked python -m dashboard
```

访问 http://127.0.0.1:8082/health，响应 `{"status":"ok"}`。Ctrl+C 停止。
端口可通过 ONETITLE_PORT 指定（1024–65535），启动前确认未占用；不自动停止其它进程。
环境必填且只接受 local/test，当前只绑定本机回环地址。`.env.example` 仅作变量说明，不自动读取 .env。

## 验证

```powershell
uv run --locked pytest -q
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked mypy src tests
```

依赖精确版本保存在 uv.lock。不得复制旧系统配置、凭据或数据到本工程。

## 完整数据库测试

本机 MySQL 启动后运行：

```powershell
uv run --locked python scripts/local_mysql.py test
```

每项 MySQL 集成测试使用随机独立库和临时账号，结束自动清理，不操作本地运行库的管理员或数据。可同时运行多个测试进程。直接运行 pytest 且未通过本地工具显式提供测试权限时，数据库集成测试会跳过，不能将其视为完整数据库验收。进程强杀可能残留随机测试资源，后续测试不会复用它们。详见 docs/REVIEW-S1-04-FOLLOWUP.md。
