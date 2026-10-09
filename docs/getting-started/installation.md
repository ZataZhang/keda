# 一键安装 kc CLI

> PyPI / Homebrew 分发包名为 `kedacode`（避免与 CNCF KEDA 混淆）。安装后有两个完全等价的命令：主命令 `kc`，以及与安装名对齐的别名 `kedacode`——装完想不起来敲什么时，直接敲包名也能用。本文其余部分统一写 `kc`。
>
> 刻意**不**提供 `keda` 命令：`bin/keda` 会与 CNCF KEDA 将来可能进入 homebrew-core 的同名 formula 抢符号链接，那时用户 `brew install` 会直接失败。

## 最快路径

**macOS（Homebrew）：**

```bash
brew install ZataZhang/tap/kedacode
kc --version
```

**任何平台（uv / pipx，含 Windows PowerShell）：**

```bash
uv tool install kedacode      # 推荐：uv 隔离环境，Windows 原生可用
# 或
pipx install kedacode
```

**curl 一键脚本（macOS / Linux）：**

```bash
curl -fsSL https://raw.githubusercontent.com/ZataZhang/keda/main/install.sh | bash
kc --version
```

脚本默认从 GitHub Release tarball 安装（`--source auto`）；`--source pypi` 改为从 PyPI 安装已发布的 `kedacode` 包——PyPI 不可达或版本不存在时直接报错退出，绝不静默回退到 tarball。脚本会按 `uv → pipx → pip --user` 的优先级选择安装方式，缺失 `uv` 时自动从 `astral.sh` 引导；不需要 `sudo`。

## 常用参数

| 参数 / 环境变量 | 作用 |
| --- | --- |
| `--version <tag>` | 锁定具体 release tag，例如 `--version v0.2.0`。PyPI 来源时映射为精确版本 pin（`v0.2.0` → `kedacode==0.2.0`）。 |
| `--source auto\|pypi\|tarball` | 安装来源：`auto`/`tarball` 走 GitHub tarball（默认），`pypi` 走 PyPI。 |
| `--method uv\|pipx\|pip` | 强制使用指定安装器。 |
| `--check` | 打印安装计划但不做任何修改。 |
| `--uninstall` | 卸载 `kedacode` tool，以及 `kc` / `kedacode` 两个命令入口。 |
| `KEDA_VERSION` | 等价于 `--version`。 |
| `KEDA_SOURCE` | 等价于 `--source`。 |
| `KEDA_PYPI=1` | `--source pypi` 的旧别名（向后兼容保留）。 |
| `KEDA_INSTALL_METHOD` | 等价于 `--method`。 |

## 初始化仓库与用户级 Skills

安装完 `kc` 之后，进入任意 Git 仓库执行：

```bash
git init
kc init
```

`kc init` 会：

1. 写入仓库根目录的 `.kedacode.toml`。
2. 管理 KedaCode 所需的 `.gitignore` 条目。
3. 同步标准 GitHub label（`agent`、`rework-prd` 等）。

`prd` 与 `code-reviewer` 不随 wheel 分发。`kc init` 会从远程
[`zata-codes-template`](https://github.com/ZataZhang/zata-codes-template) 下载且仅下载这两个
Skill，再安装到用户级目录；不会写入项目内 `.claude/skills`、`.codex/skills` 或
`.kimi-code/skills`。安装目标由 agent 注册表（各 agent 的 `auth_home`）派生：所有**已存在
配置目录**的 agent（如 `~/.codex`、`~/.claude`、`~/.kimi-code`）的 `skills/` 子目录都会各装
一份；全部缺失时回退到注册表首个 agent。设置 `CC_SWITCH_SKILLS_DIR` 可显式覆盖为单一目录。
因此该步骤需要能够访问 GitHub。

## 容器化运行（可选）

`kc` 还提供 `kc container` 子命令组，把 agent runner 跑进 Docker 容器：

- 容器内预装 claude / codex / kimi 三个 agent CLI + gh + Node + uv + just + git，避免污染本机工具链。内置注册表里另有 `codebuddy` / `qoder` / `opencode`，但**容器镜像尚未预装它们**——容器里选中这些 agent 会在启动子进程时报可执行文件不存在；需要时请在本机跑，或自行在镜像里补装。
- 认证通过 `kc container auth import` 一次性快照到 `~/.kedacode/container-auth/`，与本机 cc-switch 当前 profile 隔离——本机切账号不影响容器内 agent 认证。
- 目标仓库挂载进容器，agent 在挂载目录的 `.iar-worktrees/` 建 worktree，宿主机可直接 `kc worktree open` 接管。
- runner 容器资产（Dockerfile / compose / .env.example）随 `kc` 包发布，无需克隆 keda 源码，全局安装后即可使用。

最小启动流程：

```bash
# 1. 准备认证（本机 cc-switch 切到要给容器用的账号）
kc container auth import

# 2. 准备 GitHub token（macOS keychain 容器读不到）
export GH_TOKEN="$(gh auth token)"

# 3. 启动容器 runner
kc container up --repo /absolute/path/to/your-repo --repo-id keda

# 4. 查看日志 / 停止
kc container logs
kc container down
```

完整说明见 `docs/guides/agent-runner.md` 的「容器化运行」章节。Docker 未安装时该子命令返回明确错误，不影响本机 `kc daemon` 用法。

## 启动时版本更新检查

每次启动 `kc` 时（`kc --version`、`--help` 等只读入口除外）会对照 PyPI 上的最新发布版本，发现新版后**仅在交互式终端**（stdin 与 stderr 都是 TTY）提示并询问是否升级；确认后用与你实际安装方式匹配的命令执行升级（`uv tool upgrade kedacode` / `pipx upgrade kedacode` / `brew upgrade kedacode` / `python -m pip install --upgrade kedacode`）。识别不出安装方式（如源码 editable、tarball 直链安装）或你拒绝自动执行时，只打印对应安装器的可复制命令，不会猜测执行。

要点：

- **缓存**：检查结果写入 `~/.kedacode/update-check.json`，默认 24 小时内不再访问 PyPI；升级后已安装版本变化会让旧缓存自动失效。
- **离线容忍**：访问 PyPI 的超时只有 2 秒，失败即静默继续，不影响本次命令的退出码与输出。
- **机器路径零打扰**：以下场景更新检查整段不发生（不联网、不提示、不询问）——`--json` / `--output json` 机器模式、shell 补全协议、`--help` / `-h`、`--version`、非交互终端（管道 / CI / agent 子进程）。
- **显式关闭**：设置 `KEDACODE_NO_UPDATE_CHECK=1`（旧名 `IAR_NO_UPDATE_CHECK` 同样生效）永久停用；想手动升级时按上方安装方式对应的升级命令操作即可。

## 排错

- `command -v kc` 没命中：把 `~/.local/bin` 加入 `PATH`，或在新 shell 中重试。
- macOS GUI 终端未继承 `PATH`：在 shell rc 中显式追加 `export PATH="$HOME/.local/bin:$PATH"`。
- 安装器报 Python 版本过低：升级到 Python >= 3.11，或使用 `uv python install 3.12` 后重试。

## 卸载

```bash
bash install.sh --uninstall
```

会清理 `kedacode` tool 目录，以及 `~/.local/bin/` 下的 `kc` 与 `kedacode` 两个入口。

uv / pipx / Homebrew 安装的用户直接用对应工具卸载：

```bash
uv tool uninstall kedacode      # 或 pipx uninstall kedacode / brew uninstall kedacode
```
