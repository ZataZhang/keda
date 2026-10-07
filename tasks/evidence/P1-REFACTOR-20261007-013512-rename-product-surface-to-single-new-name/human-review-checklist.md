# 人工验收清单 · 产品表面改名为 KedaCode / `kc`

- PRD：`tasks/pending/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.md`，横幅 🧍 待人工验收（尚未归档，归档归 runner）。
- Issue：https://github.com/ZataZhang/keda/issues/228 ｜ 分支 `issue-228`（worktree `/Users/zata/code/keda/.iar-worktrees/issue-228`）。
- 状态：**执行侧已交付**——§9 本组之外的 32 项 `[x]`、2 项 `[~]`（等 runner 门禁）；十条 oracle 全部跑绿且有负控。**等你对下面 8 项表态**：3 个 Part A 决策 + 1 项呈递过目 + 4 条 Change Log 追认。这 8 项就是 PRD §9 `Human-Confirmed` 组的 8 个空框。
- 证据报告：`open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.evidence-report.md"`
- 交互版（可点选、最后生成一段 Markdown 回给 agent）：同目录 `human-review-checklist.html`（**本地文件，`.gitignore` 只放行 `tasks/evidence/**/*.md`，所以 HTML 与 5 张截图都不进版本控制**——合并后在主仓库只剩这份 Markdown 底稿；在本 worktree 里 `just prd review` 实测解析到 HTML），**本机 Chrome 实开自检 20 条断言全绿**（绑定文档就是本清单本身、零 `pageerror`、零未捕获异常、首屏卡片可见、5 张截图全部解码成功、8 张卡各自成组且组间不与他卡复用、可翻页并在末页生成非空 Markdown），且三处就地注入实测都能变红，自检命令与负控见文末「交互版自检」。
- 打开本清单：`just prd review tasks/pending/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.md`（槽位内 `.html` 优先于 `.md`）

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时一句"八项都同意"就够。也可以直接在交互 HTML 里选完、复制末尾生成的 Markdown 粘回对话。**在你回复之前，这 8 个空框保持 `- [ ]`，执行工具不会代勾。**

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 主命令 `kc`、显示名 KedaCode、派生名统一 `kedacode` 拼写 | §2 决策一 → §9 Human-Confirmed 第 1 项 | 名字已经分发到别人机器和 GitHub 历史里，改第二次比第一次贵得多；若你其实想要 `keda`，现在不说，之后要再走一轮弃用 |
| 2 | `iar` 长期保留为弃用别名（无移除日期），`iar:` 前缀 GitHub 标记永久不改 | §2 决策二 → §9 Human-Confirmed 第 2 项 | 与仓库上一次"硬切换、不留别名"的先例相反。若你要的是彻底消失，代码里那层兼容逻辑和永久保留清单就是白留；若标记被改名，新旧 runner 互相看不见、同一 Issue 会被认领两次 |
| 3 | 状态与配置新旧双读 + 显式迁移命令，及四条拒绝/保留规则 | §2 决策三 → §9 Human-Confirmed 第 3 项 | 自动迁移会在 daemon 运行中途搬动它正在写的数据库；硬切换会让 `~/.iar` 的注册表与历史在升级后"看似消失"。两条都比你批准的方式更伤 |
| 4 | §9.1 呈递区两项（rv-2 全流程、rv-9 界面截图）已亲眼看过 | §9.1 → §9 Human-Confirmed 第 4 项 | 没看就确认，前 3 项的"验收"就只剩机器背书；第 1、2 项恰好依赖界面与迁移实况 |
| 5 | 追认：占用检测的作用域收窄到同一个家目录 | Change Log「占用检测按家目录作用域」 | 决策三原话是"有任何 runner 进程在跑就拒绝"。放宽后，别的账号或临时沙箱里的自有进程不再阻止搬迁——真实机器上多一个动手窗口 |
| 6 | 追认：rv-2「占用时磁盘零变化」的比对清单排除 SQLite `-wal` / `-shm` | Change Log「rv-2 磁盘不变量范围界定」 | 若你认为伴随文件也该锁住，那么占用场景的正确实现现在正在被判绿；反之，写进不变量会让正确实现判红 |
| 7 | 追认：rv-10 的复跑命令由六项全量门禁裁剪为「残留守卫 + 四个测试文件」 | Change Log「rv-10 复跑命令的预算披露」 | 这是验证保真度的一次让渡：verifier 复跑时不会重跑 lint / 全量 pytest / mkdocs / 前端构建，那部分结论由已录制日志承担 |
| 8 | 追认：22:32 那轮 lint 红归因为「取证时并发编辑工作树」，整轮作废重跑 | Change Log「rv-10 的 lint 红来自取证并发」 | 若不认同该归因，这一轮红应按**交付缺陷**处理，需要重跑全量门禁复核——arch 违规若是真的，第 8 项会把它洗成噪声 |

**术语先解释一句**

- *派生名*：由产品名衍生出来的标识符——配置文件 `.kedacode.toml`、状态目录 `~/.kedacode`、环境变量前缀 `KEDACODE_`、skill `kedacode-operator`、插件入口点分组 `kedacode.agent_output_protocols`。
- *弃用别名*：旧命令 `iar` 仍可执行、行为与 `kc` 相同，只在人直接敲它时多打一行改名提醒；不设移除日期。
- *GitHub 标记*：runner 写进 Issue / PR 评论里的隐藏 HTML 注释（`<!-- iar:claim -->`、`iar:event` 等）和 Webhook 签名头 `X-IAR-Signature`，是跨版本、跨机器互相识别的协议文本。
- *双读*：程序同时认新旧两套位置与名字，新名优先，只有旧名时沿用旧名并提醒；不自动搬任何东西。
- *占用检测*：`kc config migrate` 动手前扫一遍有没有自有进程在跑（进程名、锁文件、受管登记三路），有则拒绝、退出码 5、磁盘零变化。
- *oracle / rv-N*：PRD §7.6 里事先写好、能逐条判真假的验收判据，rv-1…rv-10；每条都有实跑命令与负控。
- *负控*：在改动前的基线代码上跑同一脚本，确认它会红——这样"绿"才不是空跑。
- *verifier*：独立复核证据的 agent，只读，结论 `PASS` / `REJECT`。
- *`[~]`*：PRD §9 里"等 runner 门禁"的标记，不是"已完成"。
- *代码树指纹*：证据绑定用的 `h<HEAD tree>+d<非 tasks/ 改动>+u<未跟踪代码>` 标识，改代码会移、改交付文档不会移，用来证明"证据对应的正是被交付的那棵树"。

---

## 决策一 · 主命令叫 `kc`，所有派生名统一用 `kedacode` 拼写

**你在拍板的事**：`kc` 作为主命令、KedaCode 作为显示名、`kedacode` 作为长拼写入口，并且凡是需要长名字的地方（配置文件名、状态目录、环境变量前缀、skill 名、插件分组）一律写 `kedacode`，**不写** `KC_` / `.kc`；同时**不新增** `keda` 命令。**如果判错**：命令名一旦发出去就进了别人的脚本、shell 历史和 GitHub 讨论，第二次改名比第一次贵；反过来，如果派生名当初用 `kc`，`KC_` 前缀与 Keycloak 等工具撞车、`.kc` 目录已有现成占用，冲突会长期存在。

**PRD 原话（§2 决策一）**：

> 建议主命令用 `kc`，产品显示名 KedaCode；凡是需要一个「长名字」的地方——仓库配置文件 `.kedacode.toml`、本机状态目录 `~/.kedacode`、环境变量前缀 `KEDACODE_`、operator skill `kedacode-operator`、插件入口点分组 `kedacode.agent_output_protocols`——一律用包名 `kedacode` 的拼写，而不是 `kc`。原因是两个字母的前缀太容易撞车：`KC_` 是 Keycloak 等工具常用的环境变量前缀，`.kc` 目录与文件也有现成占用；`kedacode` 与 PyPI 包名一致，搜得到、不会撞。`kedacode` 本身也是一个完全等价的命令入口。不新增 `keda` 命令，避免与 CNCF 的 KEDA（Kubernetes 事件驱动扩缩容）同名。
>
> 需要你知道的代价：`kc` 是不少 kubectl 用户的 shell 别名，shell 别名优先于真正的命令，这类用户敲 `kc` 会进到 kubectl——他们要么改别名，要么改用 `kedacode`（迁移文档会写自查方法）。另外临时运行时因为包名与命令名不同，要写成 `uvx --from kedacode kc`。
>
> **请确认：** 主命令定为 `kc`、显示名 KedaCode、所有派生名统一用 `kedacode` 拼写（不用 `KC_` / `.kc`、不新增 `keda` 命令），可以吗？

**展开说**

- 这些名字在代码里**只有一个定义点**：`src/backend/core/shared/models/product_identity.py` 的 `PRIMARY_COMMAND_NAME = "kc"` / `LONG_COMMAND_NAME = "kedacode"` / `LEGACY_COMMAND_NAME = "iar"`，其余读点全部经解析函数取值。
- `kubectl` 别名冲突已被正面处理：迁移文档写了自查方法（§6「kubectl 别名自查」），并给了 `kedacode` 作为不受别名影响的等价入口。
- 明确不涉及：不改 GitHub 仓库名、不改 PyPI 包名（`kedacode` 就是包名）。

**证据**（rv-1 打包安装后的真实三入口对比；`rv-1-entrypoint-parity.txt`）：

```text
installed_binaries: iar kc kedacode
PASS version: 三个入口 stdout 相同、退出码 0            （--version / schema --json / registry list --json / registry list 四条命令各一行 PASS）
kc --version stdout: kc 0.2.1
iar --version stdout: kc 0.2.1
schema --json 根命令名: kc
全部命令结束后的临时 HOME 顶层条目（新进程观察）: .kedacode  Library   （无 .iar）
```

- **新名字面量单点定义**（PRD §7.4 检索输出）：`kc` / `kedacode` 字面量只出现在 `product_identity.py`，其余位置经解析函数取值。
- **负控**：基线树（改名前）构建的 wheel **没有 `kc` 可执行文件**，同一脚本在定位 `kc` 那一步失败；基线 `iar --version` 打印 `iar` 而不是 `kc`。见 `rv-1-negative-control-baseline.txt`。
- **复跑（可选）**：`bash tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/scripts/rv-1-entrypoint-parity.sh`

**你的回复**：`同意` / `有差异：<说明>`

---

## 决策二 · `iar` 长期保留为弃用别名，`iar:` 前缀标记永久不改

**你在拍板的事**：`iar` 命令继续可用、**不设移除日期**，只在人直接敲它时打一行改名提醒（`kedacode` 作为等价长名不提醒）；同时 Issue / PR 评论里的 `<!-- iar:… -->` 标记、Webhook 签名头 `X-IAR-Signature`、agent 执行标记、shell 补全环境变量 `_IAR_COMPLETE`、`.gitignore` 托管块文本、容器服务名 `iar-runner` **全部永久保留原样**。**如果判错**：标记改名会让不同版本、不同机器的 runner 互相看不见——同一个 Issue 被认领两次，或旧认领被当成空闲而二次派发；这是本 PRD 里唯一"看起来只是字符串、实际上是跨版本协议"的一类。

**PRD 原话（§2 决策二）**：

> 建议 `iar` 命令继续可用、不设移除日期，只在人直接敲它时提醒改名；`kedacode` 作为等价长名不提醒。写在 GitHub Issue / PR 评论里的 `<!-- iar:… -->` 标记、Webhook 签名头名称、agent 执行标记、shell 补全使用的环境变量名、`.gitignore` 托管块文本全部永久保留原样——它们是不同版本、不同机器之间互相识别的协议，改了就会出现新旧 runner 互相看不见、同一个 Issue 被认领两次。
>
> 这与仓库上一次同类改名「硬切换、不留别名」的先例相反。差别在于上次改的是仓库内部概念，这次改的是已经分发到别人机器上、写进别人脚本和 GitHub 历史里的外部契约。代价是代码里会长期保留一层很薄的兼容逻辑和一份永久保留清单，旧名不会从代码里完全消失。
>
> **请确认：** 接受 `iar` 长期作为弃用别名（无移除日期），并接受 `iar:` 前缀的 GitHub 标记永久不改名，可以吗？

**展开说**

- **永久保留清单是文档化的一等公民**：`docs/guides/migrating-from-iar.md` §7 逐条列出哪些旧字符串刻意留下、为什么；残留守卫 G1–G8 反过来检查"该改的都改了、该留的一个没少"。
- **提醒只在正确的场景**：只有以 `iar` 启动且非机器可读模式才提醒，输出走 stderr 且恰好一行；`--json` 命令的 stderr 不带提醒（避免污染管道）。
- **代价是长期的**：代码里保留一层薄兼容逻辑，旧名不会从代码库完全消失，`bare_iar` 计数在迁移文档页按设计为 26 且不参与零残留判定。

**证据**（rv-3 标记逐字节兼容 + 回放；`rv-3-marker-compat.txt`）：

```text
基线快照 vs 最终树快照 diff:（空，逐字节相同）
标记种类集合相同
tests/test_marker_compat_replay.py: 未过期的旧 iar:claim 被识别为已占用、不触发回收与二次认领
```

- **三入口一致性**（rv-1）：`iar` 与 `kc` 同输入 stdout 与退出码逐字节相同，只多一行提醒；提醒次数在 `--version` 与 `registry list` 各为 1、在两条 `--json` 命令为 0。
- **负控**：在仓库内的临时副本里把 fixture 的 `iar:claim` 改成 `kedacode:claim`（真实 fixture 校验和未动，`f60a606c…`）后重放，退出码 **1**，5 条用例报红——包括 `test_claim_judgement_keeps_baseline_occupancy_semantics`（未过期认领不再被识别为已占用）与 `test_marker_writers_still_emit_baseline_bytes[claim]`。见 `rv-3-marker-compat.txt` §6。
- **复跑（可选）**：`bash tasks/evidence/.../scripts/rv-1-entrypoint-parity.sh && uv run pytest -o addopts= -q tests/test_marker_compat_replay.py`

**你的回复**：`同意` / `有差异：<说明>`

---

## 决策三 · 状态与配置新旧双读，只在你显式执行 `kc config migrate` 时迁移

**你在拍板的事**：升级后**什么都不自动搬**；程序同时认新旧两套位置与名字，新名优先，只有旧名时沿用旧名并提醒。真正的搬迁由 `kc config migrate` 完成，并遵守四条硬规则：① 有任何自有 runner 进程（无论用哪个名字启动）在跑就拒绝；② 新旧两个状态目录都是独立真实目录时拒绝、不自动合并；③ 新旧位置不在同一文件系统时拒绝、不做跨盘复制；④ 成功后在旧路径留一个指向新目录的链接。仓库配置文件只改名、不提交。**如果判错**：替代方案是"升级时自动迁移"（会在 daemon 运行中途搬动它正在写的数据库与锁文件）或"硬切换只认新名"（`~/.iar` 里的注册表与历史在升级后看似消失）。双读本身的代价是过渡期存在两套名字的优先级规则。

**PRD 原话（§2 决策三）**：

> 建议升级后不自动搬任何东西：程序同时认新旧两套位置与名字，新名优先，只有旧名时沿用旧名并提醒；真正的搬迁由 `kc config migrate` 完成。迁移命令有四条硬规则：有任何 runner 进程（无论用哪个名字启动）在跑就拒绝；新旧两个状态目录都是独立的真实目录时拒绝、不自动合并；新旧位置不在同一个文件系统上时拒绝、不做跨盘复制；成功后在旧路径留一个指向新目录的链接。仓库配置文件只改名、不提交，由你决定何时提交。
>
> 替代方案是「升级时自动迁移」或「硬切换只认新名」：前者会在 daemon 运行中途搬动它正在写的数据库与锁文件；后者会让 `~/.iar` 里的注册表与历史在升级后看似消失。双读的代价是过渡期内存在两套名字的优先级规则，所以「新名优先 + 冲突必警告 + 不静默退回默认值」被写成了硬性验收。
>
> **请确认：** 接受「双读 + 显式迁移命令」的方式，以及上面四条拒绝 / 保留规则，可以吗？

> ⚠️ 注意：规则 ① 的实际作用域已在实现中被收窄到同一个家目录，见下面**追认第 5 项**——那是本轮唯一改动决策三语义的地方，请一并表态。

**证据**（rv-2 全流程真实命令，`rv-2-migrate-lifecycle.txt` 118 行，逐段带退出码）：

```text
## 1. 迁移前：只有旧目录的机器照常工作，每条命令只提示一次
kc registry list --json 退出码: 0；条目: [{"repo_id": "demo", ...}]
合并输出中的迁移提示次数: 1（预期 1）
HOME 顶层条目: .iar Library

## 2. 占用场景：有自有进程在跑时拒绝迁移，磁盘零变化
桩进程: 名为 kc 的进程 pid=56759，名为 iar 的进程 pid=56761
kc config migrate 退出码: 5（预期 5）
  KedaCode process(es) still running (PID 56759, 56761); stop the daemon and
  close the console before migrating. Nothing was changed.

## 3. 预演：退出码 0 且不写盘        kc config migrate --dry-run 退出码: 0
## 4. 正式迁移                        kc config migrate 退出码: 0
  move <TMPHOME>/home/.iar -> <TMPHOME>/home/.kedacode
  link <TMPHOME>/home/.iar -> .kedacode (relative)
## 5. 磁盘形态      lrwxr-xr-x .iar -> .kedacode ；仓库配置校验和迁移前后相同（8f4d3cc2…）
## 6. 迁移后新起 console 读取同样的历史      3 条记录逐字节相同：PASS
## 7. 幂等：重复执行报告已迁移、零改动      退出码 0
## 8. 新旧两个独立真实目录并存：拒绝合并、退出码 5、零改动   PASS
```

- 跨文件系统拒绝与旧路径已有真实链接（非目录）时的拒绝，由 `tests/test_state_home_migration.py` 覆盖，并在全量 pytest 中通过。
- **负控**：基线树 wheel 没有 `kc`；改用基线 `iar` 执行 `config migrate` 时不搬状态目录、HOME 下不出现 `.kedacode`，脚本在链接断言处报红（`rv-2-negative-control-baseline.txt`）。
- **本机真实状态目录**：`~/.kedacode` 目前不存在、`~/.iar` 未被本轮读写过（由隔离探针证实，见追认第 7 项的复跑命令输出）。也就是说**你的机器还没有执行过迁移**，这套流程只在临时 HOME 沙箱里跑过。

**你的回复**：`同意` / `有差异：<说明>`

---

## 呈递过目 · §9.1 人读呈递区（截图 / 自验，二选一或都做）

**你在拍板的事**：确认 §9.1 那两行呈递物你**已经亲眼看过**——迁移全流程的实际输出，和管理终端 / 迁移文档改名后的界面。这一项不是对某个方案的表态，而是"机器绿了之外，人确实看过一眼"。**如果判错**：前三个决策的"验收"就只剩机器背书，而界面文案和迁移实况恰恰是人才会注意到的部分（比如提醒出现得太吵、某页还有旧名）。

**PRD 原话（§9 Human-Confirmed 第 4 项）**：

> - [ ] §9.1 呈递区各项已亲眼看过（截图 / 自验，二选一或都做）

### 要看的东西和位置

```bash
STEM=/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name
open "$STEM"                     # 证据报告、验证计划、全部 rv-*.txt、门禁六步日志与 14 张 rv-9-*.png 成对截图
open "$STEM/rv-2-migrate-lifecycle.txt"
```

截图为 `real user flow` 层级：已安装的 `kc console` + 系统 Chrome 无头（1440x900），截图与文本取自渲染后的 DOM，不是组件预览页、不注入状态。**图片被仓库 `.gitignore` 白名单排除在版本控制之外，在 GitHub 上不显示，只能在本机打开。**

**① 管理终端首页 / 设置 / 统计 / 仓库页改名（rv-9，9 个实现页面点 + 5 个基线对照点）**

![实现：标题 KedaCode — Agent Runner 管理终端，正文独立 iar 计数为 0](rv-9-root.png)
> 本地图片，GitHub 上不显示。
```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-root.png"
```

![实现设置页：关于 KedaCode 管理终端，含 .kedacode.toml 与 kc console](rv-9-settings.png)
> 本地图片，GitHub 上不显示。
```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-settings.png"
```

![实现统计页空态：提示 kc run](rv-9-stats.png)
> 本地图片，GitHub 上不显示。
```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-stats.png"
```

![实现仓库页：扫描本地 KedaCode 仓库](rv-9-repositories.png)
> 本地图片，GitHub 上不显示。
```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-repositories.png"
```

期望值（逐页，来自 `rv-9-ui-copy.txt` 的渲染 DOM 抓取；旧值来自同一驱动在基线树上跑出的负控 `rv-9-negative-control-baseline.txt`）：

| 页面 | 旧（基线，实测报红） | 新（实现，实测通过） | 独立 `iar` 计数（旧 → 新） |
|---|---|---|---|
| 首页 `/` | 标题 `iar — Agent Runner 管理终端` | `KedaCode — Agent Runner 管理终端` | `1 → 0` |
| 设置 `/app/settings/` | 「关于 iar 管理终端」 | 「关于 KedaCode 管理终端」，正文含 `.kedacode.toml` 与 `kc console` | `4 → 0` |
| 统计 `/app/stats/` | 空态提示 `运行 iar run` | 空态出现 `kc run` | `2 → 0` |
| 仓库 `/app/repositories/` | 「扫描本地 IAR 仓库」 | 「扫描本地 KedaCode 仓库」 | `1 → 0` |

另有 4 个只在实现侧的交互点：填入扫描根后的扫描结果、扫描未找到提示、目录选择对话框（仓库徽标与 `kc` 文案），以及迁移文档页。

**这一组同时有负控**：同一个驱动、同一套断言改用基线树（改名前）的 `iar console` 重跑，逐页报 `FAIL-ITEM … 浏览器标题不含 KedaCode`、`正文里独立的 iar 单词计数为 1/4/2`，脚本退出码非 0——即断言确实能判红，不是恒真。

**② 迁移文档页（按设计保留旧名，这是唯一一处旧名合法出现的界面）**

![迁移文档页：新旧对照表、切换步骤、永久保留清单](rv-9-docs-migrating.png)
> 本地图片，GitHub 上不显示。
```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-docs-migrating.png"
```

期望值：页内有新旧对照表（§1.1–1.6）、切换 runbook（§3）、`kc config migrate` 详解与拒绝条件（§4）、kubectl 别名自查（§5）、Homebrew tap 手动步骤（§6）、永久保留清单（§7）。该页按设计保留旧名，旧名出现次数由**两种口径**统计：浏览器驱动按独立单词计数为 `bare_iar=26`，脚本按 `'iar '` 子串计数为 28——它不参与零残留判定，残留守卫 G1 用 `-g '!docs/guides/migrating-from-iar.md'` 显式排除这个文件（`scripts/rv-10-residue-guard.sh:20`）。这是守卫的放行口径，不是漏改。

**你的回复**：`已逐项过目，界面事实确认` / `有差异：<哪一项、怎么改>`

---

## 追认 5 · 占用检测的作用域收窄到「同一个家目录」

**你在拍板的事**：决策三规则 ① 写的是"有任何 runner 进程（无论用哪个名字启动）在跑就拒绝"。实现把它收窄成：**只统计同一个家目录里**以自有名字启动的进程；别的家目录（临时 HOME 沙箱、别的账号）里的自有进程**不再阻止搬迁**。自身与祖先进程仍排除；家目录读不到的进程按同家目录处理（宁可拒绝）。**如果判错**：真实机器上多了一个动手窗口——如果另一个账号下真有一个正在写同一批文件的 runner（例如共享 NFS 家目录），现在不会拦住你。反过来说，不限定家目录的话，任何带产品名的无关进程都会永久阻止迁移。

**Change Log 原话（§Change Log「占用检测按家目录作用域」，2026-10-07）**：

> - Before: 进程扫描只要命令行里出现自有名字就算占用。
> - After: 只统计**同一个家目录**里以自有名字启动的进程；别的家目录（临时 HOME 沙箱、别的账号）里的自有进程不阻止搬迁。自身与祖先进程仍排除。
> - Reason: RV 脚本在同一次运行里既起临时 HOME 的桩进程又跑迁移命令，不限定家目录会让迁移永远判为「占用」，也会误伤真实机器上恰好带产品名的无关进程。
> - Impact: `state_home_migration.py` 的扫描条件与对应单元用例。
> - Review: **待人确认**：决策三写的是「有任何 runner 进程（无论用哪个名字启动）在跑就拒绝」，本条把作用域收窄到同一个家目录——跨家目录（临时沙箱、其他账号）的自有进程不再阻止搬迁。

**证据**（`tests/test_state_home_migration.py::test_command_scan_only_counts_processes_serving_the_target_home`）：假进程表里放 5 个候选——同家目录的 `kc daemon`（命中）、另一个家目录的 `kc console`（不命中）、家目录读不到的 `kedacode run`（保守命中）、命令行里恰好出现 `kc` 的 `vim kc`（不命中）、自身进程（不命中）；断言结果恰为 `{2001, 2003}`。

**负控面**：占用场景本身（同家目录有 `kc` + `iar` 两个桩进程）在 rv-2 §2 里实测退出码 5、输出列出两个 PID 与锁文件 PID、磁盘零变化——即"该拦的还拦得住"。

**你的回复**：`同意` / `有差异：<说明>`

---

## 追认 6 · rv-2「占用时磁盘零变化」的比对清单排除 SQLite `-wal` / `-shm`

**你在拍板的事**：oracle 里"占用时磁盘零变化"这条断言，比对清单限定为两个状态目录的**条目清单（名称 + 文件校验和）**与仓库配置文件本身；SQLite 的 `-wal` / `-shm` 伴随文件**不计入**，因为 console 进程活着的时候它们本来就会变。主数据库文件与注册表仍逐字节比对。**如果判错**：如果你认为伴随文件也该锁住，那么"占用时磁盘零变化"这句话在 rv-2 里被悄悄放宽了，一条看起来严格的断言实际没管住它字面覆盖的范围。

**Change Log 原话（§Change Log「rv-2 磁盘不变量范围界定」，2026-10-07）**：

> - Before: 「两个状态目录与仓库配置文件的清单与校验和不变」。
> - After: 不变量清单限定为 `~/.iar` / `~/.kedacode` 两个目录的条目清单（名称 + 文件校验和）与仓库配置文件本身；SQLite 的 `-wal` / `-shm` 伴随文件不计入，因为 console 进程存活时它们本来就会变化。
> - Reason: 占用场景需要先有一个活着的 console/runner，SQLite 伴随文件必然出现在清单里；把它写进不变量会让正确的实现也判红。
> - Impact: rv-2 占用与预演两步的比较口径。
> - Review: **待人确认**：把「磁盘清单与校验和不变」排除 SQLite 的 `-wal` / `-shm` 伴随文件，属于对 Part A 样例「占用时磁盘零变化」的口径收窄；主数据库与注册表本身仍逐字节比对。

**证据**（`scripts/rv-2-migrate-lifecycle.sh:118-131`，清单构造原样）：

```bash
# SQLite 的 console.db-wal / console.db-shm 是连接打开期间的瞬时旁路文件，随时可能被
find "$state_dir" -type f ! -name '*.db-wal' ! -name '*.db-shm' -print0
```

rv-2 §2 实测：`占用场景列出 kc / iar 两个桩进程与锁文件 PID、磁盘零变化：PASS`。

**你的回复**：`同意` / `有差异：<说明>`

---

## 追认 7 · rv-10 的复跑命令由「六项全量门禁」裁剪为「残留守卫 + 四个测试文件」

**你在拍板的事**：证据清单里 item 10 声明给 verifier 复跑的命令，从六项全量门禁（残留守卫 + lint + 全量 pytest + mkdocs + 前端 typecheck + 前端 build）**裁剪**为「残留守卫 G1–G8 + 四个直接相关测试文件」。六项全量门禁**确实跑过并且全绿**，结论以已录制日志（`rv-10-gates.txt` + `rv-10-gates-step1..6.log`）承担，只是 verifier 复跑时不会重跑它们。**如果判错**：这是验证保真度的一次让渡。若门禁之后代码又被动过，verifier 的复跑不会发现 lint / 全量回归的退化——除非它自己去读那份日志或手动放宽预算重跑。

**Change Log 原话（§Change Log「rv-10 复跑命令的预算披露」，2026-10-07）**：

> - Before: rv-10 的 real_entry 是「残留守卫 + lint + 全量 pytest + mkdocs + 前端 typecheck + 前端 build」六项门禁。
> - After: 清单里 item 10 的 `command` 只放「残留守卫 + 四个直接相关测试文件」…六项全量门禁作为已录制证据保留在 `rv-10-gates.txt` 与分步日志里，`risks` 字段披露该裁剪与原因。
> - Reason: runner 对每条命令的复跑上限是 300 秒（`validation.reexecute_timeout_seconds`），全量 pytest 单独就要 169 秒，加上 mkdocs 严格构建与两次前端构建必然超时；超时会被判成真实失败，把「行为没做对」和「预算不够」混成一类。
> - Review: **待人确认**：…这是验证保真度的一次让渡（runner 300 秒复跑预算所致），若要求复跑全量，需放宽预算或把门禁拆成多条 oracle。

**证据**：已录制的全量门禁六步退出码全 0（`rv-10-gates.txt`）：

```text
## 1. 残留守卫 G1–G8   退出码: 0    PASS G1 … PASS G8
## 2. SKIP=check-test-flag just lint --full   退出码: 0
## 3. 全量 pytest       3394 passed, 1 skipped in 169.71s
## 4. uv run mkdocs build --strict   退出码: 0
## 5. pnpm --dir frontend-public typecheck   退出码: 0
## 6. pnpm --dir frontend-public build       退出码: 0
门禁后重算代码树指纹与报告头逐字节相同
```

item 10 实际声明的复跑命令（约 30 秒，在预算内）：

```bash
bash tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/scripts/rv-10-residue-guard.sh \
  && uv run pytest -o addopts= -q tests/test_product_identity.py tests/test_state_home_migration.py \
     tests/test_marker_compat_replay.py tests/test_cli_config_migrate.py \
  && bash tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/scripts/rv-10-isolation-probe.sh
```

**你的回复**：`同意` / `有差异：<说明>`

---

## 追认 8 · 22:32 那轮 lint 红归因为「取证时并发编辑工作树」，整轮作废重跑

**你在拍板的事**：22:32 那一轮 rv-10 在第 2 步 `just lint --full` 报了红：pre-commit 输出 `Check architecture layer dependencies....Failed / - hook id: check-architecture / - files were modified by this hook`，而同一个钩子自己打印「✅ 架构依赖方向全部合法，无违规」。我把它归因为**我自己在钩子执行窗口内往 `tasks/pending/` 追加 Change Log**（取证编排失误），整轮作废、隔离重跑取绿，22:32 那轮的记录**没有保留在证据里**。**如果判错**：如果这个归因错了——即架构违规是真的——那这一轮红就是一次被洗掉的交付缺陷，§9 的 rv-10 项与「四层依赖方向」那条 Architecture Acceptance 都建立在被误判的绿上。不认同归因时，正确处置是按交付缺陷处理并要求重跑全量门禁复核。

**Change Log 原话（§Change Log「rv-10 的 lint 红来自取证并发，隔离重跑后全绿」，2026-10-07）**：

> - Before: 22:32 那轮 rv-10 在第 2 步 `SKIP=check-test-flag just lint --full` 报红：pre-commit 输出 `Check architecture layer dependencies....Failed / - hook id: check-architecture / - files were modified by this hook`，而该钩子自己打印「✅ 架构依赖方向全部合法，无违规」。同一分钟内我在往 `tasks/pending/` 追加 Change Log。
> - After: rv-10 单独重跑，期间对工作树零写入。六步退出码全 0（…3394 passed / 1 skipped in 169.71s…），门禁后重算的代码树指纹与报告头逐字节相同；`rv-10-gates.txt` 只保留这一轮 122 行记录，22:32 那轮整份作废。
> - Reason: `hooks/shared/check_architecture.py` 全文只做检查与 `sys.exit(0 if passed else 1)`，没有任何写文件动作；pre-commit 的判定口径是「钩子执行窗口内观察到的工作树改动」，因此我把并发改文档这件事误记成了产品侧架构违规。这类红如果按报告头的 rc 采信，就会把一次取证编排失误写成一条交付缺陷——与 `.next` 构建锁那条同一教训：**门禁跑的时候不许碰工作树**。
> - Review: **待人确认**：一次门禁红被归因为「取证时并发编辑工作树」并整轮作废、隔离重跑取绿。归因依据（架构钩子只做检查、不写文件）写在条目正文与证据报告 §5；若不认同该归因，这一轮红应按交付缺陷处理，verifier 可要求重跑全量门禁复核。

**归因依据可自查（两条，都不依赖我的叙述）**：

1. `hooks/shared/check_architecture.py` 全文只读不写：`grep -c 'write' hooks/shared/check_architecture.py` 返回 **0**，唯一的文件动作是 `python_file.read_text(encoding="utf-8")`，末尾只 `sys.exit(0 if final_check_result.passed else 1)`。既然钩子自己不可能改动任何文件，pre-commit 那句 "files were modified by this hook" 只能来自钩子执行窗口内的**外部写入**。
2. 隔离重跑的 `rv-10-gates-step2.log` 里架构钩子那一行是 `Passed`，且同轮四份关键日志的指纹一致。

**可复核的处置**：要求 verifier 重跑一次全量门禁即可独立证伪——

```bash
SKIP=check-test-flag just lint --full   # 期间不要碰工作树
```

**你的回复**：`认同归因` / `不认同，按交付缺陷处理并要求重跑全量门禁`

---

## 执行者尚未替你核对的（不要替我补绿）

- **install-smoke 工作流结论**：PRD §9 有 2 项改写成 `[~]`（`[~]` 表示"等 runner 门禁"，不是已完成）。它们等的是 PR 创建后 GitHub Actions 的 `install-smoke` 跑动结果——本仓库该工作流的触发器包含 `pull_request`，而提交与推送归 runner。rv-8 的离线项已全绿。
- **真实家目录迁移**：你的 `~/.iar` 还在，`~/.kedacode` 尚不存在。本轮所有迁移验证都在临时 HOME 沙箱里跑；是否在你自己的机器上执行 `kc config migrate` 由你决定。
- **本仓库内还有旧名**：`.iar/`、`.iar-worktrees/`、GitHub 标记、`X-IAR-Signature` 等按决策二刻意保留，不是漏改。

---

## 交互版自检（本清单交付前实跑，不是「应该能打开」）

驱动脚本：`tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/scripts/validate-review-html.mjs`（本机 Chrome `headless=new` 经 CDP 打开 `file://`，逐条断言，任一 FAIL 即退出码 1）。
负控驱动：`scripts/negctrl-review-html.py`（把下面三处注入逐条跑一遍，副本跑完即删，正本哈希不变）。

```bash
STEM=/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name
node "$STEM/scripts/validate-review-html.mjs" --html "$STEM/human-review-checklist.html"
python3 "$STEM/scripts/negctrl-review-html.py"
```

实跑结果（20 条断言全 OK，2026-10-07 复跑；截图平铺进证据目录根后重跑，连跑 3 次均绿）：

```text
OK   求值绑定的就是待验证文件本身（实际 file:///Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/human-review-checklist.html）
     title=人工审查 · 产品表面改名为 KedaCode / kc（Issue #228） bodylen=19645
OK   控制台零 error/warning 条目（实际 0）
OK   零未捕获异常（实际 0）
OK   卡片总数 9（8 项决定 + 1 结果卡，实际 9）
OK   进度点 9（实际 9）
OK   首屏卡片可见且有非空标题
     首屏标题：第 1 项 · 决策一：主命令 kc，派生名统一 kedacode 拼写
OK   5 张内嵌截图全部真实解码成功（file:// 相对路径可达）
OK   8 张决定卡各自成组（组内 name 统一）、组间不与他卡复用、共 17 个选项（实际统一组 8/8，互异 name 8/8，选项 17）
OK   第 1 次「下一步」翻到第 1 张卡（实际 1）
OK   第 2 次「下一步」翻到第 2 张卡（实际 2）
OK   第 3 次「下一步」翻到第 3 张卡（实际 3）
OK   第 4 次「下一步」翻到第 4 张卡（实际 4）
OK   第 5 次「下一步」翻到第 5 张卡（实际 5）
OK   第 6 次「下一步」翻到第 6 张卡（实际 6）
OK   第 7 次「下一步」翻到第 7 张卡（实际 7）
OK   第 8 次「下一步」翻到第 8 张卡（实际 8）
OK   末页（结果卡）可见
OK   选择答案后结果页生成非空 Markdown
OK   Markdown 含标题、所选项与备注（13 行）
     ---- 生成的 Markdown 预览 ----
     # 人工审查结果 · 产品表面改名为 KedaCode / kc（Issue #228）

     第 1 项 · 决策一：主命令 kc，派生名统一 kedacode 拼写：同意：主命令 kc、显示名 KedaCode、派生名统一 kedacode，不新增 keda
     第 2 项 · 决策二：iar 长期保留为弃用别名，iar: 前缀标记永久不改：（未选择）
OK   localStorage key 已实例化（实际 iar-review-rename-product-surface-to-kedacode）
RESULT: PASS: 交互清单浏览器自检全部断言通过
```

**自检本身能不能变红？** 三处就地注入缺陷（在正本副本上改一处，跑完即删；下面是本轮实测反应）：

| 注入 | 断言反应（实测） |
|---|---|
| 结果卡脚本 `const RESULT_INDEX = cards.length - 1;` → `… - 1,;`（模板注释警告的 `SyntaxError` 整页空白故障） | `零未捕获异常` 实际 2、`进度点` 实际 0、`首屏卡片可见` FAIL、8 次翻页全部 `实际 -1`、`末页可见` FAIL、`生成非空 Markdown` FAIL、`Markdown 含标题…` 实际 1 行 → `RESULT: FAIL（14 条断言未通过）`，退出码 1。（`卡片总数 9` 仍 OK：DOM 里的卡还在，坏的是驱动它们的脚本——这正是人眼看不出、必须翻页才能抓到的故障。） |
| 第 2 卡**单个** radio `name="q2" value="0"` → `name="q1" value="0"`（与第 1 卡同名；浏览器按 name 互斥，选一张卡会取消另一张的勾选） | `8 张决定卡各自成组…` FAIL（实际统一组 7/8，互异 name 7/8，选项 17）→ `RESULT: FAIL（1 条断言未通过）`，退出码 1 |
| 一处截图 `src="rv-9-stats.png"` → 不存在的 `rv-9-MISSING-stats.png` | `控制台零 error/warning 条目` 实际 2（`net::ERR_FILE_NOT_FOUND`）、`5 张内嵌截图…解码成功` FAIL → `RESULT: FAIL（2 条断言未通过）`，退出码 1 |

**这轮自检自己也被证伪过两次，两处都已加固（不是把结论改宽）：**

1. **绑定错了文档**：脚本原先取 `/json/list` 的 `targets[0]`。实测这台 Chrome 启动时已有 4 个目标（2 个 `browser_ui`、1 个扩展 `background_page`、1 个 `page`），一旦排序把扩展页顶到第一位，全部求值就跑在扩展页上——现 2 张卡的清单被判成 `卡片总数 实际 0`，整轮红。现在改为先用 `PUT /json/new?file://…` 为待验证页面**单独开一个 tab**、按该 tab 自己的 `webSocketDebuggerUrl` 绑定，并把「求值绑定的就是待验证文件本身」升级成第 1 条硬断言（实际值逐次打印）。绑定错就不可能再报绿，也不可能再误报红。
2. **断言过弱**：原先按「全局有几个不同 `name`」数分组。整组改名能抓到（旧记录红 1 条属实），但**单点漏改抓不到**——本轮把第 2 卡其中一个 radio 改成 `q1`，组数仍是 8、选项总数仍是 17，旧断言报了 `RESULT: PASS`。现在逐卡断言（本卡 name 同组 + 该 name 不与他卡复用），同一注入红 1 条。

因此上面第 2 行的注入原文与旧记录不同（旧：整组 `name="q2"` 全改；新：只改一个 radio），旧的那次注入在本轮同样变红。这条差异记录在 PRD Change Log「交互清单自检的两处加固」一条，理由：不改就说明「自检全绿」会被读成比实际更强的保证。
