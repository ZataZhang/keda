# rv-3 / rv-5 / FR-10 负控与坏输入取证（真实入口，exit 码均为直接观测）

全部用 `IAR_CONFIG` 指向临时配置 + PATH 注入假 bin，走真实 CLI 入口；未启动任何 agent 进程。

## 1. 未注册 agent 名（doctor）—— exit=1
```
[red]doctor failed:[/] Agent 'no-such-agent' is not registered. Registered
agents: codex, claude, kimi, pi. Add an [agent_runner.agents.no-such-agent]
block to config.toml / .iar.toml.
```

## 2. bin 不在 PATH（doctor）—— exit=1
```
[red]doctor failed:[/] executable 'literal-test' (agent 'literal-test') not found in PATH.
```

## 3. 引用未注册协议（doctor）—— exit=1
```
[red]doctor failed:[/] Unknown output protocol 'no-such-protocol'. Registered
protocols: claude-stream-json, pi-json-lines, plain.
```

## 4. 引用未注册展开器（doctor）—— exit=1
```
[red]doctor failed:[/] Unknown expander 'no_such_expander' in
agents.sandbox-less.profiles.run.expand. Known expanders: git_writable_roots.
```
（负控：把展开器名改成未注册字符串，doctor 非零退出并列出全部已命名展开器——rv-3 negative control 绿侧达成）

## 5. --agent 传未注册名（iar ask，真实 CLI 参数校验）—— exit=2
```
Usage: iar ask [OPTIONS] PROMPT
Try 'iar ask -h' for help.

Error: Invalid value for '--agent': 'no-such-agent' is not one of 'auto', 'codex', 'claude', 'kimi', 'pi'.
```
（Typer 枚举取值即注册表派生：报错列出全部已注册 agent，不静默落到默认 agent——FR-10）
