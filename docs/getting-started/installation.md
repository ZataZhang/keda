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
- Compose 日志最多保留 5 个 10 MiB 文件；当前 Docker host 的缓存回收由运营者先运行 `kc container gc --dry-run` 检查，再用 `kc container gc --apply` 明确执行。候选也可能来自该主机上的其它本地项目；此清理不会移除 volume 或活动容器使用的镜像。
- daemon 的 `hosted_maintenance_enabled` 默认关闭；仅托管部署显式开启时，在 worker 边界清理安全的关闭 Issue worktree、14 天前的 Issue 原始输出日志和 90 天前已完成的 run/attempt 摘要。可用磁盘低于 10 GiB 时暂停领取新 Issue，恢复到 15 GiB 后继续；当前任务不被清理流程终止。活动标签、dirty/unmerged worktree、远端分支、审计与队列设置均受保护。

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

每次启动 `kc` 时（`kc --version`、`--help` 等只读入口除外）会对照 PyPI 上的最新发布版本，发现新版后**仅在交互式终端**（stdin 与 stderr 都是 TTY）提示并询问是否升级；确认后用与你实际安装方式匹配的命令执行升级（`uv tool upgrade kedacode` / `pipx upgrade kedacode` / `brew upgrade kedacode` / `python -m pip install --upgrade kedacode`），并在执行后复核已安装版本，只有确实换到新版才报升级成功。`brew upgrade` 只给真正由 Homebrew formula 安装的副本（安装路径位于 `Cellar/kedacode/…`），跑在 Homebrew Python 里的 pip 安装不会被误当成 brew 托管。识别不出安装方式（如源码 editable、tarball 直链安装）或你拒绝自动执行时，只打印对应安装器的可复制命令，不会猜测执行。

要点：

- **只提示正式版**：PyPI 最新版是预发布版（`rc` / `beta` / `dev`）时不提示，因为默认安装器不会装它。
- **缓存**：检查结果写入状态目录的 `update-check.json`，通常是 `~/.kedacode/update-check.json`；若只有旧目录 `~/.iar` 存在，则写入 `~/.iar/update-check.json`，直到迁移状态目录。默认 24 小时内不再访问 PyPI；升级后已安装版本变化会让旧缓存自动失效。
- **提示频率**：拒绝自动升级、安装方式无法识别而显示候选命令、或升级命令执行完却什么都没装上时，同一版本在当前缓存有效期内不会重复提示；缓存过期后会重新检查并按结果提示。
- **离线容忍**：访问 PyPI 的超时只有 2 秒，失败即静默继续，不影响本次命令的退出码与输出。
- **机器路径零打扰**：以下场景更新检查整段不发生（不联网、不提示、不询问）——`--json` / `--output json` 机器模式、shell 补全协议、`--help` / `-h`、`--version`、无参数调用（只打印用法）、非交互终端（管道 / CI / agent 子进程）。
- **显式关闭**：设置 `KEDACODE_NO_UPDATE_CHECK=1`（旧名 `IAR_NO_UPDATE_CHECK` 同样生效）永久停用；想手动升级时按上方安装方式对应的升级命令操作即可。
## 托管模式（可选，每客户独立 VM）

托管模式把 runner 跑在客户专用 Linux VM 上，并用一个**需要认证的 HTTPS Console** 管理运行凭据。它完全可选——**默认 `enabled = false`，本地 `kc daemon` / `kc console` / `kc container` 免费路径不受影响，本机 console 仍只绑 `127.0.0.1` 且无需登录**。开启托管需设 `[agent_runner.hosted].enabled = true` 与 `public_base_url`（必须 HTTPS；明文 http 仅限本机反向代理自检），并由运营者预先准备 checkout、配置唯一的 `repository_id` 与绝对 `repository_path`。

Hosted Runner 也可选择启用 Linux 虚拟桌面：`[agent_runner.hosted].desktop_enabled = true` 会让 Agent 在该 VM 的 Runner 容器内实际操作任意可启动的 Linux GUI，并按任务要求录制 WebM 与动作轨迹。它默认关闭，不开放 VNC/远程桌面端口，也不会自动录制所有任务；配置和命令说明见 Agent Runner 指南。

首次设置顺序（详见 `docs/guides/agent-runner.md` 的「托管模式」章节）：

1. **引导码由运营者在客户 VM 本地签发**：`kc console bootstrap-token create --expires-in 24h`。原码只打印一次、经独立渠道转交、服务端仅存哈希、单次消费、到期作废；再次 `create` 使上一个未消费码失效；托管未开启时该命令退出 `1`。Console **不开放自助注册**。
2. **客户用引导码在网页首次设置管理员口令**（≥12 位，服务端存 PBKDF2 摘要；会话走 HttpOnly cookie，默认 8 小时）。同一来源 15 分钟内最多尝试登录 5 次，超限后按 `Retry-After` 等待；未认证或会话失效后所有凭据读写一律 401。
3. **按 Console 指引创建单仓库 fine-grained GitHub PAT**：入口 `https://github.com/settings/personal-access-tokens/new`，仓库范围 Only select repositories，精确权限 `Metadata: read` / `Contents: read/write` / `Issues: read/write` / `Pull requests: read/write`；保存即只读校验，权限不足/不可见/待审批/格式错误一律拒绝且不启用 runner。
4. **粘贴 Claude / Codex / Kimi 的 provider API key**：入口与计费前置条件见 Console（Anthropic Console / OpenAI platform / Kimi platform 的 API Keys 页）；保存不调模型，「测试连接」需显式确认费用后才发一次最小真实请求（会产生上游费用）。
5. **凭据按仓库加密保存、只经 tmpfs 原生认证文件交付给匹配 runner**：设置与验证阶段只保存密文；显式启动 runner 时才解密到 Linux tmpfs。轮换前先停 runner 并清除旧文件，停止失败时保留旧凭据。secret 不进 URL、浏览器存储、SQLite、日志、Compose environment、`docker inspect` 环境值或 argv；页面只回状态不回显 secret。
6. GitHub PAT 与 Agent key 均验证后，客户可在 Console 点「启动 runner」。宿主服务只启动预先配置且 `origin` 与 PAT 仓库一致的 checkout；runner 使用按仓库命名的 Docker volume 持久化运行历史、审计、队列状态和日志，不挂载宿主全局状态目录。Console「停止 runner」会清除 tmpfs 明文并保留加密凭据。运营者须把 `runner_auth_root` 放在 Linux tmpfs 上。

归属与撤销：**口令/会话属客户；GitHub PAT 属其 GitHub 账户；provider key 属其计费账户；GitHub Actions secrets、项目 DB 凭据、SSH 私钥、反向代理 TLS 私钥都不由该 Console 收集**（由客户/运营者在自有基础设施管理）。**Console 删除只停该仓库 runner 并清本地密文/tmpfs 副本，不代替你撤销上游 key/PAT**——上游仍有效，需你主动到 provider/GitHub revoke。信任边界：同一 runner 内的 agent/代码可读取该 runner 自己收到的 key，隔离发生在「按仓库 + 单租户 VM」层。运维侧：项目级 DB 检查仍由 PR CI 把关；daemon 可用磁盘低于 10 GiB 暂停领取、越过 15 GiB 恢复、不杀在跑任务；`hosted_maintenance_enabled` 默认关。退订：删本地凭据 → 撤销上游 key/PAT → 停 runner 与代理 → 数据保留期按确认值执行。

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
