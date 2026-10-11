# 从 `iar` 迁移到 KedaCode（`kc`）

本产品的命令入口已从 `iar` 改名为 **`kc`**（显示名 **KedaCode**，等价长名 `kedacode`）。
PyPI 包名 `kedacode` 与 GitHub 仓库名 `keda` **不变**，也**不新增** `keda` 命令（避免与 CNCF
KEDA 撞名）。

改名是**对外换名 + 内部双读**，不是硬切换：`iar` 命令、`IAR_*` 环境变量、`~/.iar` 状态目录、
仓库里的 `.iar.toml`、已安装的补全脚本、旧插件入口点分组在**不做任何操作时继续工作**，只有旧名
存在时给一行提示；新旧冲突一律**新名优先且警告**；读不到旧值时**不静默退回默认值**。真正的搬迁
只在显式执行 `kc config migrate` 时发生。

> 想快速验证而不改动本机？用 `uvx --from kedacode kc <命令>` 临时运行新版即可。

---

## 1. 新旧对照表

### 1.1 命令与入口

| 类别 | 旧 | 新 | 迁移后是否仍可用 |
|---|---|---|---|
| 主命令 | `iar` | `kc` | 是，`iar` 作为**永久弃用别名**保留，不设移除日期 |
| 等价长名 | `kedacode` | `kedacode`（不变） | 是 |
| 临时运行 | `uvx --from kedacode iar` | `uvx --from kedacode kc` | 两者都能跑 |

`kc`、`kedacode`、`iar` 三个入口指向同一实现，相同输入下**标准输出与退出码逐字节一致**；只有人在
终端直接敲 `iar`（人类可读、非补全）时，错误输出里多一行改名提醒，`kedacode` 不提醒。

子命令集合、旗标与退出码都没有变化，把命令名换成 `kc` 即可逐条照抄：

| 旧命令 | 新命令 |
|---|---|
| `iar init` | `kc init` |
| `iar daemon` | `kc daemon` |
| `iar loop` | `kc loop` |
| `iar run` | `kc run` |
| `iar config migrate` | `kc config migrate` |

### 1.2 环境变量（10 个，逐一对应）

解析顺序：`KEDACODE_<X>` 优先，`IAR_<X>` 兜底；两者都设且取值不同以 `KEDACODE_*` 为准并警告一次，
取值相同则静默；仅设旧名时旧值生效并提示一次。

| 旧变量 | 新变量 | 用途 |
|---|---|---|
| `IAR_CONFIG` | `KEDACODE_CONFIG` | 机器级配置文件路径 |
| `IAR_CONSOLE` | `KEDACODE_CONSOLE` | 管理终端相关配置 |
| `IAR_HOME` | `KEDACODE_HOME` | 容器内状态目录（仅镜像内使用） |
| `IAR_REPO_ID` | `KEDACODE_REPO_ID` | 当前仓库注册 id |
| `IAR_SKILLS_DIR` | `KEDACODE_SKILLS_DIR` | Skill 安装根目录 |
| `IAR_PRD_SKILL_PATH` | `KEDACODE_PRD_SKILL_PATH` | 指定 prd skill 的 `SKILL.md` |
| `IAR_LOOP_DAEMON_INTERVAL` | `KEDACODE_LOOP_DAEMON_INTERVAL` | loop daemon 轮询间隔 |
| `IAR_IDEA_INBOX_INBOUND_SECRET` | `KEDACODE_IDEA_INBOX_INBOUND_SECRET` | Idea Inbox 入站签名密钥 |
| `IAR_SKIP_GH_AUTH_CHECK` | `KEDACODE_SKIP_GH_AUTH_CHECK` | 跳过 gh 鉴权检查 |
| `IAR_NO_UPDATE_CHECK` | `KEDACODE_NO_UPDATE_CHECK` | 关闭 CLI 启动时的 PyPI 更新检查 |

由本产品派生的子进程（管理终端托管进程、factory 启动的 agent、容器）会**同时**拿到新旧两个变量名，
因此旧代码读 `IAR_*`、新代码读 `KEDACODE_*` 都不会失败。

### 1.3 状态目录与仓库配置文件

| 用途 | 旧 | 新 |
|---|---|---|
| 本机状态目录 | `~/.iar` | `~/.kedacode` |
| 仓库配置文件 | `.iar.toml` | `.kedacode.toml` |

- 状态目录：新目录 `~/.kedacode` 优先；只有旧目录 `~/.iar` 的机器原样继续用旧目录（不复制、不新建），
  每条命令提示一次可迁移。全新机器只创建 `~/.kedacode`。
- 仓库配置：`.kedacode.toml` 优先、`.iar.toml` 兜底；只有旧文件时读写都落在旧文件且日常不刷屏提示
  （仅 `kc init` 与迁移预演提示可改名）；两者并存时以新文件为准并警告；新建用 `.kedacode.toml`。

### 1.4 operator skill 与插件入口点分组

| 用途 | 旧 | 新 |
|---|---|---|
| 随包 operator skill | `iar-operator` | `kedacode-operator` |
| 插件入口点分组 | `iar.agent_output_protocols` | `kedacode.agent_output_protocols` |

`kc init` 安装新的 `kedacode-operator` skill，并在旧 `iar-operator` 副本与某个历史随包版本**完全一致**
（未被改动）时清理它；被改过的副本保留并提示。已注册在旧分组 `iar.agent_output_protocols` 的第三方插件
继续被发现，新分组优先；同一协议 id 在两个分组都注册时以新分组为准。

### 1.5 shell 补全文件

| Shell | 旧文件 | 新文件 |
|---|---|---|
| zsh | `~/.zsh/completions/_iar` | `~/.zsh/completions/_kc` |
| bash | `~/.config/iar/iar_completion.bash` | `~/.config/kedacode/kc_completion.bash` |
| fish | `~/.config/fish/completions/iar.fish` | `~/.config/fish/completions/kc.fish` |

`kc completion install` 后补全对 `kc`、`kedacode`、`iar` 三个名字都生效，**已经装好的旧补全脚本继续可用**。
补全使用的环境变量名保持 `_IAR_COMPLETE` 不变（见第 6 节）。

### 1.6 容器

| 用途 | 旧 | 新 |
|---|---|---|
| 容器内状态目录 | `/home/runner/.iar` | `/home/runner/.kedacode`（并建链接 `/home/runner/.iar -> .kedacode`） |
| 容器状态目录变量 | `IAR_HOME` | `KEDACODE_HOME` |
| 仓库 id 变量 | `IAR_REPO_ID` | compose 同时传 `KEDACODE_REPO_ID` 与 `IAR_REPO_ID` |
| 镜像内入口脚本 | `iar-runner-entrypoint.sh` | `kedacode-runner-entrypoint.sh` |
| 启动命令 | `CMD ["iar", "daemon"]` | `CMD ["kedacode", "daemon"]` |
| **容器服务名** | `iar-runner` | **不变**（用户的 compose 操作依赖它，永久保留） |

`kc container up` 注入的宿主挂载改为**当前实际生效**的状态目录（迁移前是 `~/.iar`，迁移后是
`~/.kedacode`）。绕过 `kc container up` 直接执行 `docker compose` 时，缺少宿主状态目录变量会直接报错并
提示用法，而不是挂错目录。服务 / 容器 / 镜像名 `iar-runner` 保持不变，`docker compose` 操作习惯不受影响。

---

## 2. 双读优先级一览（不改任何东西时的行为）

- 环境变量：`KEDACODE_X` → `IAR_X` → 默认值（读不到旧值不静默退回默认值）。
- 仓库配置：`.kedacode.toml` → `.iar.toml`。
- 状态目录：`~/.kedacode` → `~/.iar`。
- 所有提示与警告**只写 stderr**，绝不进入机器可读输出（`--json` 等）的 stdout。

---

## 3. 本机切换 runbook

升级后什么都不做也能继续用旧名；准备好后按下面顺序切换（**在 keda 仓库之外的目录执行**，见下方注意）：

```bash
# 1. 停掉正在运行的 daemon（避免迁移与运行中进程竞争）
kc daemon stop          # 或旧名 iar daemon stop；关闭终端进程亦可

# 2. 拉取新版本（以源码安装为例）
git pull
# 3. 重装 CLI，让可编辑安装生成 kc 入口
just reinstall-kc

# 4. 预演迁移，只报告计划、不写盘（与正式执行同一套前置检查）
kc config migrate --dry-run

# 5. 正式迁移本机状态目录 + 当前仓库配置文件改名
kc config migrate

# 6. 提交被改名的仓库配置文件（命令不会自动 git add / commit）
git add .kedacode.toml && git rm --cached .iar.toml 2>/dev/null; git commit -m "chore: rename repo config to .kedacode.toml"

# 7. 用新名重新启动 daemon
kc daemon
```

> **在 keda 仓库自身的注意**：keda 仓库的配置文件**暂时仍叫 `.iar.toml`**（模板同步脚本只排除
> `.iar.toml`，改名会在模板迁移时被覆盖）。在 keda 里执行 `kc config migrate` 时仓库步骤会把它改名——
> 建议 runbook 的仓库步骤**在 keda 仓库外执行**，或用 `--repo` 指向其他仓库、在 keda 内执行后撤销该文件
> 改名，直到模板排除列表更新。`just reinstall-kc` 是 `just reinstall-iar` 的新别名，两者等价。

---

## 4. `kc config migrate` 详解

`kc config migrate` 完成两件事：**本机状态目录迁移**（`~/.iar` → `~/.kedacode`，并在旧路径留一个
相对链接 `~/.iar -> .kedacode`）与**仓库配置文件改名**（`.iar.toml` → `.kedacode.toml`）。它保留原有的
「清理旧版 init 写死的模板钉住值」行为。

要点：

- **预演与正式执行共用同一套前置检查与同一个退出码**；`--dry-run` 只报告、绝不写盘。正式执行会被拒绝的
  情况，预演同样以该退出码结束。
- 迁移成功后**不自动提交**仓库配置改名，命令结束时提醒你提交。
- 重复执行**无副作用**（第二次报告「已迁移」且什么也不改，退出码 0）。
- 只有人类可读输出，**不提供 `--json`**。

### 拒绝条件与退出码

| 场景 | 退出码 | 磁盘变化 | 说明 |
|---|---|---|---|
| 任一自有 runner 进程存活（以 `kc`/`kedacode`/`iar` 之一启动、或受管登记 / 锁文件中的存活 PID） | **5** CONFLICT | 零改动 | 拒绝动手并列出占用中的 PID |
| `~/.kedacode` 与 `~/.iar` 是两个独立的真实目录 | **5** CONFLICT | 零改动 | 不自动合并（SQLite / 锁文件无法安全合并），需人工处理 |
| 仓库内 `.kedacode.toml` 与 `.iar.toml` 并存 | **5** CONFLICT | 零改动 | 需先手工消除并存 |
| 新旧状态目录不在同一文件系统（`EXDEV`） | **1** GENERAL | 零改动 | 不做跨盘复制 |
| 进程扫描不可用（psutil 缺失） | **1** GENERAL | — | 无法确认无占用时拒绝 |
| 改名成功但建旧路径链接失败（如 Windows 权限） | **1** GENERAL | 新目录已生效 | 不回滚，输出手工补建链接命令 |
| 带 `--repo-id` | **2** USAGE | 零改动 | 建议改用 `kc config migrate --repo .` |
| `--repo` 指向非仓库目录 | **3** NOT_FOUND | — | — |
| 当前目录不在仓库内（隐式执行） | **0** | 只迁本机 | 只做状态目录迁移 |
| 仓库未初始化 | **0** | 视情况 | 跳过仓库步骤并说明 |
| 已完成迁移后重复执行 | **0** | 零改动 | 报告「已迁移」 |

> 迁移状态目录时，只改写新状态目录内机器级 `config.toml` 中以引号包裹的旧状态路径值
> （`~/.iar`、`$HOME/.iar`、`${HOME}/.iar`、家目录绝对路径形式）；其他文件里的旧路径只在报告中列出，
> 不自动改写。

---

## 5. `kc` 与 kubectl 别名冲突

!!! warning "敲 `kc` 却进了 kubectl？"

    `kc` 是不少 kubectl 用户常用的 shell 别名，而 **shell 别名优先于真正的命令**，这类用户敲 `kc` 会进到
    kubectl。用下面命令自查：

    ```bash
    type kc
    ```

    - 若输出形如 `kc is aliased to 'kubectl ...'`：要么改掉 / 去掉这个别名，要么改用等价长名
      **`kedacode`**（`kedacode <子命令>` 与 `kc <子命令>` 完全等价，且不提醒）。
    - 若输出 `kc is .../bin/kc`：说明用的是本产品的命令，一切正常。

---

## 6. Homebrew tap 手动步骤

发布 CI 生成的 Homebrew formula skeleton 只在 tap 仓库里 `Formula/kedacode.rb` **缺失**时才 bootstrap；
已存在的 formula 不会被自动补上 `kc` 符号链接。因此 brew 用户要拿到 `kc`，需**手动**给 tap 仓库的现有
formula 加一行，并更新 tap README：

1. **先发新版**：发布包含 `kc` console script 的新版本，等 `update-tap` job 把 formula 的
   `url` / `sha256` 刷到这个版本（顺序反了会让所有 brew 用户装不上，因为 `install_symlink` 目标文件还不存在）。
2. **再加符号链接**（存在性判断，避免旧版本 formula 构建失败）：

   ```ruby
   bin.install_symlink libexec/"bin/kc" if (libexec/"bin/kc").exist?
   ```

3. **更新 tap README** 把命令示例改成 `kc` / `kedacode`。

> 在你完成上述手动步骤之前，Homebrew 用户只有 `iar` 与 `kedacode` 两个入口可用（`iar` 仍是有效别名）。
> **执行者（本 PRD 的实现方）不得推送 tap 仓库**——tap 只提供你手动执行的步骤，不在本次改动范围内。

---

## 7. 永久保留清单（跨版本线上契约，永不改名）

以下 `iar` / `IAR` 拼写是不同版本、不同机器之间互相识别的协议或已提交到用户磁盘 / 仓库的内容，**永远保持
原样**，不迁移、不改名；新旧版本混跑时依赖它们避免重复认领或互相看不见的事件：

- GitHub Issue / PR 评论里的标记：`<!-- iar:claim -->`、`iar:event`、`iar:dependency`、`iar:fast-merge`、
  `iar:dependency-wait`、`iar:merge-acceptance`、`iar:pr-contract`、`iar:realistic-validation`
- 尝试历史标记：`<!-- iar-attempt-history -->`、`[iar-attempt-end]`
- agent 执行标记：`<<IAR_EXEC>>`、`<<END_IAR_EXEC>>`、`[IAR_EXEC_RESULT]`
- Webhook 签名头：`X-IAR-Signature`
- shell 补全环境变量：`_IAR_COMPLETE`
- 容器服务 / 容器 / 镜像名：`iar-runner`
- 每个受管仓库里的目录：`.iar/`、`.iar-worktrees/`、`iar-evidence/`
- `.gitignore` 托管块文本（引用时逐字保留）与段注释 `IAR-managed`：

  ```text
  # >>> iar (managed by `iar init`) >>>
  # <<< iar <<<
  ```

此外，PyPI 包名 `kedacode`、GitHub 仓库名 `keda` 均不变，也不新增 `keda` 命令。
