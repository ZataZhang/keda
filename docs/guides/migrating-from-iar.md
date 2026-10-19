# 从 IAR 升级到 KedaCode（`kc`）

当前 CLI 只提供 `kc` 与 `kedacode` 两个入口。旧 `iar` 命令、`IAR_*` 环境变量、`~/.iar` 状态目录、
`.iar.toml` 仓库配置、旧 shell 补全变量和旧插件入口点不再由运行时自动识别。升级时请完成下面的
迁移；迁移后如果仍保留旧脚本或环境变量，它们不会继续生效。

PyPI 包名 `kedacode` 和 GitHub 仓库名 `keda` 不变，也不新增 `keda` 命令。

## 更新命令、变量和插件

| 旧名称 | 当前名称 |
|---|---|
| `iar <command>` | `kc <command>`（也可用 `kedacode <command>`） |
| `IAR_<SUFFIX>` | `KEDACODE_<SUFFIX>` |
| `iar-operator` | `kedacode-operator` |
| `iar.agent_output_protocols` | `kedacode.agent_output_protocols` |
| `_IAR_COMPLETE` | `_KC_COMPLETE`（重新安装 shell 补全） |

把 shell 配置、CI、脚本和容器配置中的环境变量改为 `KEDACODE_*`。由 KedaCode 启动的子进程也只会
收到 `KEDACODE_*` 名称。第三方输出协议插件需要改用新入口点分组并重新安装。

重新安装补全脚本：

```bash
kc completion install
```

## 迁移本机状态与仓库配置

升级前先停止旧版启动的 daemon、console 和其他 runner。安装新版后先预演：

```bash
kc config migrate --dry-run --repo /path/to/repository
```

确认计划后执行：

```bash
kc config migrate --repo /path/to/repository
```

命令会把 `~/.iar` 移到 `~/.kedacode`，把仓库里的 `.iar.toml` 改名为 `.kedacode.toml`，并清理旧版
`kc init` 写入的部分固定默认值。它不会创建旧路径符号链接，也不会运行 `git add` 或提交文件。若只迁移
本机状态，可在仓库外运行 `kc config migrate`；要迁移某个仓库配置，请使用 `--repo` 指定仓库。

如果本机同时存在两个独立状态目录，或仓库中同时存在 `.iar.toml` 和 `.kedacode.toml`，命令会拒绝
自动选择或合并。先手动备份并整理两份数据，再重新运行迁移。命令也会拒绝在检测到 KedaCode 或旧版
IAR runner 进程仍运行时搬迁状态目录。

状态迁移会改写全局 `config.toml` 中指向旧状态目录的配置值；其他文件里残留的旧路径只会列出，不会
自动改写。检查报告并按需手动更新这些路径。

## 仍需保留的持久化协议

迁移运行时入口不会重命名已经写入 GitHub Issue、PR 或 agent 会话的数据格式。以下协议仍使用原名称，
无需手工改写历史记录：`iar:*` Issue/PR marker、`iar-attempt-history` / `iar-attempt-end`、
`IAR_EXEC` 会话命令块，以及 `X-IAR-Signature` Webhook 请求头。

旧版 `.iar` 或 `.iar-worktrees` 仓库内目录可能仍出现在忽略规则和历史配置中；当前运行时目录布局不因
本次命令别名移除而自动迁移。若仓库显式将证据目录配置为 `.iar/evidence`，迁移该配置前先确认已有
证据的位置和保留要求。
