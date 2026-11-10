# 来源账户本机维护与事件记录

新增本机命令 `python -m dashboard.source_manage create|update`，默认不执行，
必须显式 --confirm。使用统一受限维护配置，不提供 HTTP 写入口，不接百度。
创建时指定已有内部账户、命名空间、稳定来源 ID 和名称；更新仅支持名称与
启停，需要 --source-id 和 --expected-version。稳定身份及内部绑定不可直接改。
来源标识当前只接受 ASCII 字母、数字及 _ . : -，更广来源需确认后扩展。

示例（合成值，内部账户必须预先存在；命令需在项目 Python 环境模块路径下执行）：

```powershell
$env:PYTHONPATH = 'src'
python -m dashboard.source_manage create --account-id 1 --namespace synthetic --external-id example-001 --name 合成账户 --confirm
python -m dashboard.source_manage update --source-id 1 --expected-version 1 --active no --confirm
```

不要把密码或 API token 放入参数；本入口不维护 credential_ref，也不自动创建
内部账户。成功只返回映射编号与版本，失败不回显 SQL/输入。提交异常需通过
受控数据库查询按身份/编号核对映射和事件后重试，暂未提供查询列表 CLI。

0012 建 account_source_events，记录映射编号、动作、版本、维护者、变更字段名
与 UTC 时间，不复制名称、稳定来源 ID 或凭据。更新锁定映射、核对版本，
无变化不增加版本或事件；事件失败回滚映射。两个创建者同身份通过唯一键拒绝
重复，不静默覆盖。降级有事件则拒绝，必须停写、备份、核对后处理。

本地维护依赖 OS/文件访问权，operator 来自维护进程，不是网页认证用户。
当前数据库管理员仍可改事件，不宣称生产不可篡改审计；以后网页维护需要
管理角色校验、CSRF 防护、最小权限及独立审计授权，不能直接暴露此命令。

隔离测试验证创建、改名/启停、旧版本与绑定修改拒绝、无变化不写事件、
事件失败回滚及无确认不读凭据；全套 99 项测试通过，Ruff、格式与 mypy strict
通过。实际迁移只在随机测试库执行，运行库未升级至 0012，无真实映射变更。
两份本地数据库说明与设计、计划引用均排除在提交外。
