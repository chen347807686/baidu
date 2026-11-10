# 来源映射只读核对

新增本机 `python -m dashboard.source_inspect list|events`，使用统一维护配置，
事务显式 READ ONLY，不提供网页接口。每次默认 50、最多 100 条，按 ID 递增，
使用 --after-id 翻页；输出 next_after_id 是本页尾编号，继续同筛选查询直到空页。
不是快照导出，跨次翻页可能观察到新提交。

示例（先在项目环境设置 PYTHONPATH=src）：

```text
python -m dashboard.source_inspect list --account-id 1
python -m dashboard.source_inspect list --namespace synthetic --external-id example-001
python -m dashboard.source_inspect list --source-id 1
python -m dashboard.source_inspect events --source-id 1 --limit 20
python -m dashboard.source_inspect events --source-id 1 --after-id 20 --limit 20
```

list 只返回编号、内部账户、来源身份、启停、版本、更新时间，不选择名称或
credential_ref。来源身份仍属内部运营数据，仅供受信任维护者，不应随意转发
输出。events 返回事件编号、映射编号、动作、版本、维护者、字段名与 UTC 时间，
不含原业务值。空事件列表不证明映射存在，需同时查询映射。

可用于创建结果不确定时按唯一来源身份核对，以及更新后按版本/事件核对。
不会自动判断某次操作已成功，也不自动重试写入。当前仍使用本机维护账号，
生产最小只读账号和不可篡改审计需独立落实。

隔离库验证身份大小写、映射状态、字段排除、事件分页与只读事务读取；
参数错误回归验证不完整身份/缺少事件目标/超大分页不读取维护凭据。
完整 102 项测试通过，Ruff、格式与 mypy strict 通过。没有迁移或运行库变更。
两份本地数据库文档及本地计划变动继续排除在提交之外。
