# 看其他机器

当用户询问同一仓库里有哪些 KedaCode 机器、谁在线或机器正在处理哪个 Issue 时，运行：

```bash
kc peers --json
```

命令只读取当前仓库的 `kedacode/presence` 状态 Issue 和 `agent/running` 认领评论，不会启动 daemon 或 Agent。要人工查看可运行 `kc peers`；输出按本机时区显示时间，JSON 时间为带偏移的 UTC。

`machines` 按稳定 `machine_id` 汇总每个进程的最新状态评论。`status` 在评论超过五分钟未更新时为 `offline`。`unreported_claims` 表示运行中认领的持有者没有匹配的状态评论；旧版认领可能没有机器编号，所以会显示主机名并标注“持有者未上报状态（可能是旧版本）”。仓库尚未创建状态 Issue 时，命令提示还没有机器上报状态，同时仍会列出没有状态评论的认领持有者，并以退出码 0 返回。
