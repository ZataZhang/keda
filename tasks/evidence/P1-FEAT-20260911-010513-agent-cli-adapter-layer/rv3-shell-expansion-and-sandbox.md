# rv-3 真实入口证据（final tree，$(git rev-parse --short HEAD) 见 commit）

## 边界 A：配置不做 shell 展开

- 临时配置 `rv3-literal-config.toml`（经 `IAR_CONFIG` 注入真实配置加载路径）声明 `args = ["$(whoami)", "a|b", "*.py", "--out={cwd}"]`。
- doctor `--json`（`rv3-doctor-output.json`）：三个 shell 敏感字面量**原样**出现在 argv；`{cwd}` 占位符按闭集展开为本 worktree 绝对路径。
- 假 agent 脚本（真子进程）回显的 `sys.argv`（`rv3-fake-agent-sysargv.txt`）与 doctor/构造器输出**逐字节一致**——`assert` 双重校验（一致性 + 字面量存在）。

## 边界 B：沙箱参数缺失被摊开告警

- `sandbox-less` agent 的 `run`/`repl` 为 writable 且无 sandbox/approval 参数：doctor 退出码 0 并打印完整命令行，同时对全部四个可写 profile 打印显式 WARN（stderr）：

```
[yellow]WARN:[/] agent 'literal-test' profile 'run' is writable but declares no
sandbox/approval flag; the agent runs without local sandboxing.
[yellow]WARN:[/] agent 'literal-test' profile 'repl' is writable but declares no
sandbox/approval flag; the agent runs without local sandboxing.
[yellow]WARN:[/] agent 'sandbox-less' profile 'run' is writable but declares no
sandbox/approval flag; the agent runs without local sandboxing.
[yellow]WARN:[/] agent 'sandbox-less' profile 'repl' is writable but declares no
sandbox/approval flag; the agent runs without local sandboxing.
```

## 边界 C：坏输入 fail-fast（同轮顺带取证）

- bin 不在 PATH：`doctor failed: executable 'literal-test' (agent 'literal-test') not found in PATH.`，exit=1。
- profile 不完整：`doctor failed: agent 'literal-test' has no 'deliberate' profile (declared: run).`，exit=1（doctor 强制四用途完整声明）。
