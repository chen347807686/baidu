# OneTitle Dashboard V2

当前完成文档 PLAN 的 S0-01：独立本地最小服务。业务、数据库、前端及生产部署尚未实现。

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
