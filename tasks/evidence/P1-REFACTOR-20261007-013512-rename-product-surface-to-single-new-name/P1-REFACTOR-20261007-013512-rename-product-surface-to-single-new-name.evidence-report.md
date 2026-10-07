# 证据报告 · 产品表面改名为 KedaCode / `kc`

PRD：`tasks/pending/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.md`
GitHub Issue：https://github.com/ZataZhang/keda/issues/228
分支：`issue-228`（worktree `/Users/zata/code/keda/.iar-worktrees/issue-228`）
基线代码树：`8ff493493a919235de313de1c495ff4400a4f5a7`（`git merge-base HEAD main`，即 `36c3f734`）
被测代码树：`h8ff493493a919235de313de1c495ff4400a4f5a7+d8b0e85046983c6faabd2cab55f0de66c6ea10d00+u0e855cf868906ccea532dee5cf00978c15cc24cb`
（口径 = `h<HEAD^{tree}>` + `d<非 `tasks/` 已跟踪改动整段 diff 的 blob hash>` + `u<非 `tasks/` 未跟踪代码文件的
blob 指纹>`，即 241 个已跟踪改动文件 + 9 个未跟踪代码文件；PRD 正文、本报告、验证计划这些 `tasks/` 交付记录
不参与，所以「取证之后还在写文档」不会再让指纹自移，见 PRD Change Log「代码树指纹改为与交付记录无关」。
本轮该口径内移位过两次：① `just lint --full` 的 `ruff-format` 就地改写了新增测试 `tests/test_state_home_migration.py`
的排版（`…ue2ece0e2…` → `…u0e855cf86…`，语义无变化），对最终树再跑 ruff / ruff-format 为空操作即已收敛；
② runner 打回上一轮交付后，`.iar.toml` 的 `agent_runner.validation.evidence_dir` 从被误改的 `tasks/evidence`
回退成本仓既有的 legacy `.iar/evidence`（见 §5「RV 脚本的位置」），已跟踪段因此从 `…da60ff35…` 移到 `…d8b0e850…`。
十条 oracle 的正向证据、九条基线负控与六步全量门禁全部在这棵新树上重收（2026-10-08 01:11–01:22，逐步耗时见
`.iar/evidence/_run-status.log`）：rv-1 9s、rv-2 7s、rv-3 2s、rv-4 5s、rv-5 22s、rv-6 4s、rv-7 10s、rv-8 5s
（含附项契约 diff，由同一条命令串跑产出）、rv-9 25s、rv-10 3s；rv-10-gates 六步 194s，门禁后自检回算指纹与本报告头逐字节相同；
九条基线负控头部只记基线树 `8ff49349…`，于 01:16–01:22 逐条变红。上一轮的 22:32 那一轮 rv-10 被我在它
`just lint --full` 窗口内并发改文档撞红、作废重跑，该教训见 §5）
执行时间：2026-10-07 首收，2026-10-08 01:11–01:22 按最终树整体重收
执行器：Qoder（会话内自证），待独立 verifier 复核

> 本报告引用的 `.txt`（命令原文与输出）与 RV 脚本刻意不进代码历史：原始产物在 `.iar/evidence/`，
> **RV 脚本只在 `.iar/evidence/scripts/`**（本仓 `evidence_dir` 是 legacy `.iar/evidence`，该目录由
> `.git/info/exclude` 排除）；本呈递目录只放产物的副本——同名 `.txt`、门禁六步的 `rv-10-gates-step1..6.log`，
> 以及平铺在本目录的 14 张 `rv-9-*.png` 截图（PRD §7.6 与 §9.1 第 2 行指定的呈递位置；平铺是必需的，不是排版偏好：
> `scripts/shared/just/check_prd_evidence.sh` 只扫证据目录**第一层**的 `.png/.jpg/.jpeg/.webm`，并要求每张的 basename
> 出现在本报告的嵌图里，放进子目录会被判「前端改动但无视觉证据」而退出码 1）。
> 副本由 `.iar/evidence/scripts/sync_presentation_copies.sh` 单向同步：它只搬 `rv-*.txt` / `rv-*.png` /
> `rv-10-gates-step*.log`，遇到任何 `.sh` / `.py` / `.mjs` / `.js` 直接拒绝，并删除本目录里残留的 `scripts/`；
> 本轮同步 55 个产物、清理脚本条目 0 个，目录内已无脚本。
> 上一轮把 23 个 RV 脚本镜像到本目录 `scripts/` 并被交付门整体打回，原因写在 §5「RV 脚本的位置」；
> 那份镜像的哈希清单 `.iar/evidence/_mirror-shas.txt` 随之作废，不再作为复核入口。
> 清单 `.iar/evidence/evidence.json` 里 10 条命令全部指向 `.iar/evidence/scripts/`，复跑入口与本报告声明的位置一致。
> 截图同样不入库，因此下面的嵌图在 GitHub 上是坏图，只能在本机打开报告时看到。
> PR / CI 链接：本次交付尚未发布 PR（提交与推送归 runner），`install-smoke` 结论待 PR 发布后由
> GitHub Actions 产出，见文末「执行者尚未替你核对的」。

## 1. 人审导航（对应 PRD §9.1，只看这一节就知道要不要点开、点开看哪）

> 本轮交付时该导航已汇总成可作答的人工审查清单：同目录 `human-review-checklist.md`（静态底稿）
> 与 `human-review-checklist.html`（交互版，逐卡作答并生成可复制回 agent 的 Markdown），覆盖 §9
> `Human-Confirmed` 的 8 个空框（3 项 Part A 决策 + 1 项呈递过目 + 4 条 Change Log 追认）。
> 入口：`just prd review tasks/pending/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.md`（实测解析到交互版 `.html`）。

| # | 你要看什么（对应 oracle） | 呈递物 | 想自己复核？ |
|---|---|---|---|
| 1 | 迁移全流程：旧目录照常用 → 有 runner 时拒绝 → 预演 → 迁移 → 历史仍在 → 重复执行无副作用（rv-2） | 本目录 `rv-2-migrate-lifecycle.txt`（118 行，逐段带退出码与原样输出；关键原文摘录见下方 §1.1） | 「占用场景」段：退出码 5，列出名为 `kc` 与 `iar` 的两个桩进程 PID 与锁文件 PID；「预演」段：退出码 0 且清单校验和不变；「迁移后」段：`.iar -> .kedacode` 相对链接；「console」段：迁移前后 3 条运行记录 id 一致；「重复执行」段：退出码 0 且零改动 |
| 2 | 管理终端与迁移文档改名（rv-9） | 本目录平铺的 14 张 `rv-9-*.png`（5 组基线↔实现同名对照 + 4 个只在实现侧的交互/文档页面点），同一 1440x900 视口、真实渲染 DOM，每张配同名页面文本侧车 `.txt`；主报告 `rv-9-ui-copy.txt` 在本目录 | 设置页标题由「关于 iar 管理终端」→「关于 KedaCode 管理终端」；侧边栏品牌由 iar → KedaCode；仓库页卡片由「扫描本地 IAR 仓库」→「扫描本地 KedaCode 仓库」；统计空态出现 `kc run`；迁移文档页有新旧对照表与切换步骤 |

复核报告与截图（绝对路径，可直接粘贴执行）：

```bash
STEM=/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name
open "$STEM"                     # 本报告、验证计划、全部 rv-*.txt、门禁六步日志与 14 张 rv-9-*.png 截图（平铺在本目录）
open "$STEM/rv-2-migrate-lifecycle.txt"
open "$STEM/rv-9-ui-copy.txt"
```

### 1.1 迁移生命周期关键原文（rv-2，逐字摘录自证据文件）

```text
kc config migrate 退出码: 5（预期 5）
占用场景列出 kc / iar 两个桩进程与锁文件 PID、磁盘零变化：PASS
kc config migrate --dry-run 退出码: 0（预期 0）
预演磁盘零变化：PASS
kc config migrate 退出码: 0（预期 0）
  move <TMPHOME>/home/.iar -> <TMPHOME>/home/.kedacode
  link <TMPHOME>/home/.iar -> .kedacode (relative)
迁移前后 console 返回逐字节相同（3 条记录以 issue_number 101/102/103 与 started_at 为身份）：PASS
再次 kc config migrate 退出码: 0（预期 0）
重复执行零改动：PASS
两个独立真实目录并存时退出码: 5（预期 5）
双目录并存零改动：PASS
```

新进程观察到的磁盘形态（`ls -la` 与 `readlink` 原样摘录）：

```text
lrwxr-xr-x@  1 zata  staff     9 Oct  7 22:28 .iar -> .kedacode
readlink <TMPHOME>/home/.iar: .kedacode
```

### 1.2 管理终端与迁移文档成对截图（rv-9）

每张图三件套：嵌图 → 本地标注 → `open` 命令。左列基线（改名前，旧文案），右列实现（改名后）。
所有图均为**真实 user flow**：真实 wheel 内的真实静态资源 + 真实后端 + 临时 HOME 里的真实数据，
非 component preview、未注入任何状态。

首页 / dashboard —— 浏览器标题与页面品牌：

![基线：标题 iar — Agent Runner 管理终端，正文出现独立 iar](rv-9-baseline-root.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-baseline-root.png"
```

![实现：标题 KedaCode — Agent Runner 管理终端，正文独立 iar 计数为 0](rv-9-root.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-root.png"
```

`/` 与 `/app/dashboard/` 是两条入口路由，抓取到的**是同一个页面**：`rv-9-app.png` 与
`rv-9-root.png` 的 `shasum -a 256` 相同（`eaaa3107…caa3378`），基线侧同样相同（`789bed17…f20989`）。
两张图仍各自独立呈递，因为断言是逐路由分别跑的（`rv-9-ui-copy.txt` §4 两行各自 `bare_iar=0`），
证据按采集点齐全，而不是按像素去重。

![基线 /app/dashboard/：标题 iar — Agent Runner 管理终端](rv-9-baseline-app.png)

> 本地图片，GitHub 上不显示（与上面的基线首页图字节相同）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-baseline-app.png"
```

![实现 /app/dashboard/：标题 KedaCode — Agent Runner 管理终端](rv-9-app.png)

> 本地图片，GitHub 上不显示（与上面的实现首页图字节相同）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-app.png"
```

设置页 —— 「关于 … 管理终端」标题与说明里的命令名、配置文件名：

![基线设置页：关于 iar 管理终端](rv-9-baseline-settings.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-baseline-settings.png"
```

![实现设置页：关于 KedaCode 管理终端，含 .kedacode.toml 与 kc console](rv-9-settings.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-settings.png"
```

统计页空态 —— 空数据时给出的命令提示：

![基线统计页](rv-9-baseline-stats.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-baseline-stats.png"
```

![实现统计页空态：提示 kc run](rv-9-stats.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-stats.png"
```

仓库页 —— 扫描卡片标题、未找到提示、目录选择对话框徽标（生产边界：Dialog 与 Portal 都在真实页面流里打开）：

![基线仓库页：扫描本地 IAR 仓库](rv-9-baseline-repositories.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-baseline-repositories.png"
```

![实现仓库页：扫描本地 KedaCode 仓库](rv-9-repositories.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-repositories.png"
```

![实现：填入扫描根后的扫描结果](rv-9-repositories-scan.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-repositories-scan.png"
```

![实现：扫描未找到提示](rv-9-repositories-notfound.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-repositories-notfound.png"
```

![实现：目录选择对话框中的仓库徽标与 kc 文案](rv-9-repositories-picker.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-repositories-picker.png"
```

迁移文档页 —— `uv run mkdocs build --strict` 之后打开站点里的真实产物：

![迁移文档页：新旧对照表、切换步骤、永久保留清单](rv-9-docs-migrating.png)

> 本地图片，GitHub 上不显示（图片被仓库的 `.gitignore` 白名单排除在版本控制之外）。

```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-228/tasks/evidence/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name/rv-9-docs-migrating.png"
```

页面文案的机器计数（`rv-9-ui-copy.txt` 第 4 段原样摘录，`bare_iar` = 正文里独立 `iar` 单词计数，
`iar_event` = `iar:event` 标记出现次数，标记属于对外契约面、不计入旧名残留）：

```text
  rv-9-root: chars=393 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-app: chars=393 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-settings: chars=567 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-stats: chars=607 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-repositories: chars=349 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-repositories-scan: chars=349 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-repositories-notfound: chars=377 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  rv-9-repositories-picker: chars=645 bare_iar=0 iar_event=0 title='KedaCode — Agent Runner 管理终端'
  断言: 标题含 KedaCode、独立 iar 计数为 0、各页期望文案齐备: PASS
```

迁移文档页是**唯一按设计保留旧名**的页面（它的全部用途就是告诉用户 `iar` 改成了 `kc`），
计数不参与「零旧名残留」判定，单列如下：

```text
{"page":"rv-9-docs-migrating","name":"rv-9-docs-migrating","url":"file:///Users/zata/code/keda/.iar-worktrees/issue-228/site/guides/migrating-from-iar/index.html","title":"从 iar 迁移到 kc - keda","text_chars":7046,"bare_iar":26,"iar_event":8,"shot":"rv-9-docs-migrating.png"}
```

基线同一断言的红点（负控按预期变红，共 25 条 `FAIL-ITEM`，见 `rv-9-negative-control-baseline.txt`）：

```text
FAIL-ITEM rv-9-baseline-root: 正文里独立的 iar 单词计数为 1
FAIL-ITEM rv-9-baseline-root: 浏览器标题不含 KedaCode: 'iar — Agent Runner 管理终端'
FAIL-ITEM rv-9-baseline-settings: 正文里没有期望文案 '关于 KedaCode 管理终端'
FAIL-ITEM rv-9-baseline-settings: 正文里没有期望文案 '.kedacode.toml'
FAIL-ITEM rv-9-baseline-settings: 正文里没有期望文案 'kc console'
FAIL-ITEM rv-9-baseline-stats: 正文里没有期望文案 'kc run'
FAIL-ITEM rv-9-baseline-repositories: 正文里没有期望文案 '扫描本地 KedaCode 仓库'
FAIL: 管理终端文案断言未通过（见上方 FAIL-ITEM 行）
```

## 2. 一句话结论

十条 oracle 全部在最终代码树上跑绿，九条基线负控 + 一条守卫负控 + 一条契约比对器自证负控全部按预期变红；
三个入口 `kc` / `kedacode` / `iar` 逐字节同输出、提醒只跟着 `iar` 且机器模式静音，
只有旧目录的机器零操作可用、显式 `kc config migrate` 走「拒绝 → 预演 → 迁移 → 链接 → 幂等」全链且
历史运行记录迁移前后逐字节相同，GitHub 标记与改名前逐字节一致，管理终端各页的用户可见名零旧名残留
（迁移文档页按设计保留旧名对照，见 §1.2），CLI 机器可读契约 49 个命令与退出码枚举逐字段不变，
全量回归 3394 passed / 1 skipped、六项门禁退出码 0。
唯一未闭合的是 PR 上的 `install-smoke` 结论——它只能由 GitHub Actions 在发布 PR 后产出。

## 3. 自动化 oracle 结果

| Oracle | 真实入口 | 结果 | 关键退出码 | 证据 |
|---|---|---|---|---|
| rv-1 | wheel → `uv tool install` → 临时 HOME 三入口四条命令 | PASS | 首命令 0；三入口 stdout 逐字节相同 | `rv-1-entrypoint-parity.txt` |
| rv-1 负控 | 基线树同一脚本 | 按预期 FAIL | 1（缺 `kc` 可执行文件） | `rv-1-negative-control-baseline.txt` |
| rv-2 | 已安装 `kc` 的 migrate 全生命周期（含真实桩进程与真实 console HTTP） | PASS | 占用 5 / 预演 0 / 迁移 0 / 幂等 0 / 双目录 5 | `rv-2-migrate-lifecycle.txt` |
| rv-2 负控 | 基线树同一脚本 | 按预期 FAIL | 1（缺 `kc`） | `rv-2-negative-control-baseline.txt` |
| rv-3 | 基线树与最终树同一快照脚本（真实写入函数）+ 回放测试 | PASS | diff 无差异；pytest 0 | `rv-3-marker-compat.txt` |
| rv-3 负控 | 临时目录 fixture 副本改 `iar:claim` → `kedacode:claim` | 按预期 FAIL | 1 | `rv-3-marker-compat.txt` 末段 |
| rv-4 | 四次 `kc registry list --json` 环境变量组合 + 身份模块 pytest | PASS | 0/0/0/0，repo 来源 alpha/beta/beta/gamma | `rv-4-env-precedence.txt` |
| rv-4 负控 | 基线树同一脚本 | 按预期 FAIL | 1 | `rv-4-negative-control-baseline.txt` |
| rv-5 | 三个真实桩进程 + `kc registry list --json` + 四个测试文件 | PASS | 0；`running` / `unmanaged_count=3` | `rv-5-own-process-recognition.txt` |
| rv-5 负控 | 基线树同一脚本 | 按预期 FAIL | 1 | `rv-5-negative-control-baseline.txt` |
| rv-6 | 两个真实插件分发包 `--with` 安装 + `kc agent doctor --protocols --json` | PASS | 0；5 个协议 id 无重复无缺失 | `rv-6-plugin-groups.txt` |
| rv-6 负控 | 基线树同一脚本 | 按预期 FAIL | 1 | `rv-6-negative-control-baseline.txt` |
| rv-7 | 临时 HOME 预置旧副本 + 真实 `kc init` + skill 漂移测试 | PASS | 0；原样副本删除、改动副本保留并提示路径 | `rv-7-operator-skill.txt` |
| rv-7 负控 | 基线树同一脚本 | 按预期 FAIL | 1 | `rv-7-negative-control-baseline.txt` |
| rv-8 | wheel `entry_points.txt`、补全、`install.sh --check`、容器预演、Dockerfile/release 骨架 | PASS（离线项） | 0 | `rv-8-distribution.txt`；契约面另见 `rv-8-contract-diff-cli-surface-exitcodes.txt` |
| rv-8 负控 | 基线树同一脚本 | 按预期 FAIL | 1（缺 `kc = backend.api.cli:main`） | `rv-8-negative-control-baseline.txt` |
| rv-8 附项 | 基线 `iar schema --json` vs 最终 `kc schema --json`，只比机器可读表面 | PASS | 比对 0（SAME）；比对器负控 1（人为删选项报红） | `rv-8-contract-diff-cli-surface-exitcodes.txt` |
| rv-9 | 真实 `just console-sync` + 已安装 `kc console` + Chrome/CDP 真实渲染 | PASS | 各页 `bare_iar=0`；mkdocs strict 0 | `rv-9-ui-copy.txt` + 14 张 `rv-9-*.png` 成对截图 |
| rv-9 负控 | 基线树同一脚本（`--entry-binary iar`） | 按预期 FAIL | 1（停在文案断言） | `rv-9-negative-control-baseline.txt` |
| rv-10 | 残留守卫 G1–G8 + lint + 全量 pytest + mkdocs + 前端 typecheck/build | PASS | 全部 0；3394 passed / 1 skipped in 169.71s | `rv-10-gates.txt` + `rv-10-gates-step1..6.log` |
| rv-10 负控 | 残留守卫跑基线树 | 按预期 FAIL | 守卫 1（G1–G8 全红，267 处命中） | `rv-10-negative-control-baseline.txt` |
| rv-10 附项 | 隔离探针：8 个驱动已打包 CLI 的脚本都有 `export HOME=` + 宿主 `~/.iar` 在取证窗口内 0 改动 + 宿主无 `~/.kedacode` | PASS | 0 | `rv-10-isolation-probe.txt` |
| rv-10 附项负控 | `rv-10-isolation-probe.sh --negative-selftest`：临时目录里造一个时间戳落在窗口内的状态目录 | 按预期 FAIL | 自检报 `RESULT: NEGATIVE-CONTROL-OK` 且探针本体判红 | `rv-10-isolation-probe.txt` §说明 |

## 4. 执行者已替你核对过什么（人不必重复）

- **入口一致**：`kc --version` / `kedacode --version` / `iar --version` 都输出 `kc 0.2.1`；
  `schema --json` 根命令名 `kc`；四条命令三入口 stdout 逐字节相同、退出码相同。
- **提醒不越界**：改名提醒只在 `iar` 的人类可读命令里出现且恰好一次
  （`iar --version` 1 次、`iar registry list` 1 次），`iar --json` 与 `kc`/`kedacode` 全部 0 次，
  JSON stdout 永不夹带提示。
- **全新机器**：首次 `kc registry list --json` 退出码 0、输出 `[]`，临时 HOME 顶层只出现
  `.kedacode`，全程没有 `.iar`。这条同时暴露并修掉了基线的全局安装缺陷（见 PRD Change Log）。
- **迁移的安全边界**：有自有进程时退出码 5 且两个状态目录与仓库配置的清单/校验和不变；
  预演不写盘；正式迁移旧路径留相对链接；仓库配置改名且提示提交；重复执行幂等；
  新旧两个独立真实目录并存时拒绝且零改动。
- **契约不破坏**：标记快照与基线逐字段相同（只剥 provenance），旧版本未过期认领在新版本里
  仍判「已占用」；`ExitCode` 枚举与子命令集合未变（`kc schema --json` 与基线 `iar schema --json` 的机器可读表面
  ——49 个命令路径、参数/选项元数据、7 项退出码枚举——逐字段相同，唯一预期差异是根命令名与 9 处 `help` 文案；
  见 PRD §7.4 检索记录、§6 的 `rv-8-contract-diff-cli-surface-exitcodes.txt` 与 rv-1 报告）。
- **插件兼容**：旧分组 `iar.agent_output_protocols` 仍被发现，同 id 时新分组优先，内置协议不重复。
- **skill 与文档**：`kedacode-operator` 随包安装，旧 `iar-operator` 原样副本被删、改动副本保留并
  在 `kc init` 输出里给出路径；README、AGENTS.md、CLAUDE.md、`docs/` 各页与随包 skill 文案由守卫
  G1–G8 逐条检索为零残留，永久保留清单一个不少。
- **回归**：全量 pytest、`just lint --full`、mkdocs 严格构建、前端 typecheck 与 build 全绿；
  门禁前后代码树指纹一致（证明 lint 自动修复没有偷改文件）。

## 5. 对抗自检结论

- **提示是否可能进入 stdout**：不会。人类模式经 root logger 的输出被 rv-1 的 stdout 逐字节比较覆盖
  （三入口 stdout 完全相同且含表格），机器模式（`--json`）四次都解析成功且 stderr 外无提示泄漏。
- **占用检测是否漏掉以 `iar` 启动的旧版本 daemon**：不漏。rv-2/rv-5 都用名为 `iar` 的真实桩进程
  验证被识别；扫描按家目录作用域限定（PRD Change Log「占用检测按家目录作用域」），临时沙箱里的
  同名进程不会误拒真实机器上的迁移。
- **链接存在时路径比较是否把同一目录判为两个**：不会。迁移后 `.iar` 是指向 `.kedacode` 的相对链接，
  rv-2 的重复执行步骤在该形态下报告「已迁移」且零改动；双独立真实目录场景则退出码 5 并零改动。
- **子进程是否只拿到新名**：不是，双注入。rv-4 的 `tests/test_product_identity.py` 断言被本产品派生
  的子进程同时拿到 `KEDACODE_*` 与 `IAR_*` 两套名字。
- **新安装上旧名是否可能被优先使用**：不会。`kc` 排在脚本入口与 `which` 顺序首位（rv-5 断言 argv0
  优先与 `kc`、`kedacode`、`iar` 顺序，全部缺失时回落 `uv run kc`）。
- **预演是否在任何分支写盘**：不写。rv-2 预演步骤前后目录清单与逐文件校验和相同。
- **额外发现并如实记录的一处脆弱点**：rv-10 的门禁脚本本身有缺陷——用 `code-tree-fp.sh | head -1`
  取门禁后指纹会让指纹脚本收到 SIGPIPE，把「六项全绿」误判成退出码 141。已在 `.iar/evidence/scripts/`
  （不进代码 diff）改为落盘后再取首行并显式判退出码，本报告的门禁结果在修复后重跑取得；
  同类脆弱点在 5 处脚本里一并换成 `sed -n '1,Np'`（rv-3/rv-6/rv-8/rv-10 负控与守卫的命中截断）。
- **取证脚本的退出码不可单独采信**：macOS 自带 bash 3.2 在 `set -u` 下把 `$REAL_HOME（` 的全角括号首字节
  吞进变量名，脚本当场中止却返回退出码 0。rv-9 有一轮就是这样「绿」在只写了 6 行的报告上。此后编排层
  一律回读每条 RV 自己的证据文件核对终局标记（绿要 `^PASS: rv-<n>`，红要标记词），标记与行数一起记账，
  不采信 rc；这也是 rv-10 那一轮红被识别出来的前提。
- **测量仪器不等于被测产品**：无头 Chrome 在伪造 `HOME` 下 `Page.navigate` 永不回响应，而同一端口的服务端
  `curl` 1.8ms 就返回 200——是驱动层的限制，不是产品缺陷。rv-9 因此让驱动 node 进程用回真实 HOME，
  被测 `kc console`、已安装 CLI、临时仓库与数据库仍在临时 HOME 沙箱内（报告头 `browser_driver_home`
  行同时记两个 HOME）。改完基线负控第一次跑到文案断言，此前两轮都死在驱动超时、红在错误的位置。
- **门禁红是不是产品红**：22:32 那轮 rv-10 的 `just lint --full` 报 `check-architecture` 「files were
  modified by this hook」，但该钩子自己打印「✅ 架构依赖方向全部合法，无违规」且源码里没有任何写文件动作
  （`hooks/shared/check_architecture.py` 只做检查与 `sys.exit`）。同一分钟内我在往 `tasks/pending/` 的 PRD
  追加 Change Log——pre-commit 会把钩子执行窗口内观察到的任何工作树改动归给该钩子。据此把 rv-10 单独重跑、
  期间零改动工作树，六项全部退出码 0 且门禁后指纹与报告头逐字节相同，坐实红在取证编排、不在代码。
  教训与 `.next` 构建锁同类：取证期间不得并发改文件。
- **契约比对器自己也要被证伪**：rv-8 附项除了基线 vs 最终的 SAME，还在最终侧人为删掉 `init` 的一个真实选项，
  比对器必须报红（退出码 1）才算数，否则「49 个命令一致」可能是恒真断言。
- **交互清单也要在真实浏览器里被证伪**：`human-review-checklist.html` 交给 reviewer 前用本机 Chrome（`headless=new` + CDP）实开自检，20 条断言全绿（绑定文档就是待验证文件本身、零 `pageerror`、零未捕获异常、9 张卡、首屏卡片可见且高度 > 100px、5 张 `file://` 内嵌截图全部 `naturalWidth > 0`、8 张卡各自成组且组间 `name` 不与他卡复用、翻到末页生成非空 Markdown）。三处就地注入实测能变红：脚本 `cards.length - 1;` → `cards.length - 1,;`（模板注释警告的 `SyntaxError` 整页空白故障）红 14 条；第 2 卡单个 radio `name="q2"` 改成 `"q1"`（跨卡串答案）红 1 条；一处截图路径改成不存在的文件红 2 条。驱动脚本 `.iar/evidence/scripts/validate-review-html.mjs`，负控驱动 `.iar/evidence/scripts/negctrl-review-html.py`。
- **自检驱动自己被抓出来两次，两处都按「只能改严、不能改宽」处理**：① 原先取 `/json/list` 的 `targets[0]`，而这台 Chrome 启动时已有 4 个目标（2 个 `browser_ui`、1 个扩展 `background_page`、1 个 `page`），排序一变就把求值打到扩展后台页上，实测报 `卡片总数 实际 0` 整轮红——现改为 `PUT /json/new?file://…` 为待验证页面单独开 tab 并按其自身 `webSocketDebuggerUrl` 绑定，并把 `location.href` 等于目标文件升级为第 1 条硬断言；② 原先按「全局不同 `name` 数」判分组唯一性，整组改名能抓、**单点漏改抓不到**（实测注入单张卡的一个 radio 后 `RESULT: PASS`），现改为逐卡断言（组内 `name` 统一 + 组间不与他卡复用）。
- **RV 脚本的位置：交付门把「镜像」整体打回**：上一轮我判定 RV 脚本对兄弟脚本（种子、快照生成器、守卫、指纹）的引用
  一律按 `SCRIPT_DIR` 自身目录解析后，把 23 个脚本镜像进呈递目录 `tasks/evidence/<stem>/scripts/`，理由是
  `tasks/evidence/**` 已被 gitignore、镜像同样是本地文件。这个理由是错的，而且错在门禁的实现而不是口径：
  `agent_runner_git.expand_changed_path()` 会把 `git status --porcelain` 折叠出来的未跟踪**目录**再 rglob 展开成逐文件条目，
  gitignore 只影响它是否入库，不影响它是否进入判定集。于是交付门看到 17 个 `rv-*.{sh,py,mjs}` 出现在代码 diff 里，整单打回。
  本轮的修法是把 `.iar.toml` 的 `evidence_dir` 回退成本仓既有的 legacy `.iar/evidence`（脚本唯一归宿 `.iar/evidence/scripts/`，
  由 `.git/info/exclude` 排除），删掉镜像目录，清单 10 条命令重指向正本路径；呈递目录只留产物副本，并由
  `sync_presentation_copies.sh` 硬性拒绝复制 `.sh/.py/.mjs/.js`（本轮 55 个产物、0 个脚本）。
  `SCRIPT_DIR` 自身目录解析这一条保留——它让「从证据目录复跑」与「从别处复跑」等价，与位置无关。
- **取证编排自身的三处缺陷，都按「红在取证、不在产品」定位后修掉并重收**：
  ① **后台桩进程把 runner 的复跑钉死**：rv-2 / rv-5 用 `while :; do sleep 300; done` 冒充自有进程，桩继承父进程的
  stdout/stderr 管道；`kill` 只杀 sh，孤儿 `sleep` 仍持有写端，`subprocess.run(capture_output=True)` 等不到 EOF，
  runner 复跑 item 2 会永久挂起（我的编排层先撞上：驱动停在 rv-1 之后，`pgrep` 查出一个不属于任何 shell 的 `sleep 300`）。
  改为桩输出落文件 + 收尾先 `pkill -P` 收回子进程再 `kill` 再 `wait`；② **负控「红」其实是没跑起来**：驱动拼接 rv-7 负控命令时
  漏了一个空格，把 `…/iar-operator` 与 `--out` 黏成一个参数，脚本以退出码 2 报 `unknown argument`——非 0 不等于变红，
  这轮之前它被当成负控成立。参数改为逐段拼接，修完 rv-7 负控真实跑到断言层并以 `FAIL: 安装后缺少 kc 可执行文件` 变红；
  ③ **列进 `evidence_files` 的产物必须能由本 item 的命令复现**：`rv-8-contract-diff-cli-surface-exitcodes.txt` 与
  `rv-10-isolation-probe.txt` 此前由独立命令产出，清单里却挂在 item 名下，runner 复跑不会重写它们，于是留着旧指纹的内容冒充新证据。
  现把契约 diff 串进 item 8（合计 5 秒，不挤占 300 秒预算），隔离探针以 `set -o pipefail` + `tee` 串在 item 10 末尾——
  `pipefail` 是必需的，去掉它就等于用 `tee` 的退出码 0 把判红的探针洗成绿。
- **宿主并发写入让「零改动」断言失效，改成逐条归因而非放宽**：rv-10 的隔离探针原先断言取证窗口内宿主 `~/.iar` 零改动，
  但驱动本轮取证的宿主 agent-runner daemon 与 RV **并发运行**，它自己持续写 `console.db-wal`、`daemon-locks/`、
  `process-logs/**`——上一轮能取到零改动只是窗口错开的运气。mtime 无法区分「daemon 写的」与「RV 漏写宿主的」，
  所以既不能整目录判红（必假红），也不能把窗口挪回昨天（那才是真作废）。探针改为按文件名逐条归因：
  runner 自己的数据库/锁/日志标 `RUNNER-OWNED` 并单列，其余任何条目判 `RV-WRITE`；归因清单不含 `config.toml`、
  `backups`、`compat`、`container-auth`、`repos`、`skills` 与新名状态目录，也就是 RV 真漏写时必写的那几类。
  自检同步扩成三用例（非记账写入判红、只有记账写入归因放过、记账目录里混入新文件仍判红），证明它既不是恒绿也不是恒红；
  判定标记用整轮合计的 `ISOLATION-RESULT=CLEAN`，不用单目录计数行——后者在 ABSENT 分支也会打印 0，红的时候也可能被 substring 命中。

## 6. 与锁定契约的 diff

- 标记快照：基线树 vs 最终树逐字段相同（`rv-3-marker-compat.txt`，diff 无差异）。
- `kc schema --json` vs 基线 `iar schema --json`：49 个命令路径两侧一一对应，参数/选项的名字与元数据、
  退出码枚举（7 项）逐字段相同；唯一预期差异是根命令名 `iar -> kc`，以及随之改动的 9 处 `help` 人类文案
  （只计数、不参与判定）。`ExitCode` 源码 diff 33 行，全部是命令名字面量与 docstring，码表数值未动。
  证据：`rv-8-contract-diff-cli-surface-exitcodes.txt`（Architecture Acceptance 第 3 条；含比对器自身的负控）。
- 环境变量：9 个 `KEDACODE_*` 新名 + 旧名兜底，对照表在 `docs/guides/migrating-from-iar.md`
  （rv-9 文档页截图为证）。

## 7. 低风险门禁结果（折叠）

```text
1. 残留守卫 G1–G8                 退出码 0（PASS G1 … PASS G8）
2. SKIP=check-test-flag just lint --full   退出码 0
3. uv run pytest -o addopts= -q            退出码 0（3394 passed, 1 skipped in 172.83s）
4. uv run mkdocs build --strict            退出码 0
5. pnpm --dir frontend-public typecheck    退出码 0
6. pnpm --dir frontend-public build        退出码 0
门禁后工作树 = 报告头工作树（未被自动修复改动）
```

本轮门禁于 2026-10-08 01:19–01:22 在新指纹 `…+d8b0e850…` 上跑完整六步，194 秒，`rv-10-gates.txt` 123 行，
门禁后回算的工作树与报告头逐字节相同。历史上这一节还作废过一轮：同一指纹下 22:32 的那轮在第 2 步被我的并发文档写入撞红
（见 §5「门禁红是不是产品红」），整份作废后于 22:43 重跑。

每步完整输出单独落盘，正文只留退出码与尾部 20 行：
`rv-10-gates-step1.log` … `rv-10-gates-step6.log`（正本在 `.iar/evidence/`，本目录的同名文件是 `sync_presentation_copies.sh` 同步的产物副本；
绝对路径也写在 `rv-10-gates.txt` 各步的「完整输出」行）。
新增的 1 个用例是本轮补的 `test_command_scan_treats_a_broken_iteration_as_unavailable`（进程扫描不可用时的失败安全）。

## 8. 执行者尚未替你核对的

- PR 上 `install-smoke` 工作流对最终提交的结论（需 runner 发布 PR 后读取）。
- 合并后在真实机器上的本机切换：`kc config migrate` 一次、shell 补全重开、`~/.kedacode` 落地。
  runbook 与 Homebrew tap 手动步骤写在 `docs/guides/migrating-from-iar.md` 与 PRD §12。
- §2 三项决策与 §9.1 两项呈递物的人工确认（`Human-Confirmed` 空框由人回答）。已生成 `human-review-checklist.md` / `.html` 把这 8 项集中呈递，但**清单本身不能替人回答**：8 个空框仍由你勾选，交互版只负责收集你的答案。
- Change Log 里标了「待人确认」的 4 条执行期偏离，已在 §9 的 `Human-Confirmed` 组逐条开成追认项：
  占用检测按家目录作用域（收窄决策三）、rv-2 磁盘不变量排除 SQLite 伴随文件、rv-10 复跑命令的预算裁剪、
  一次架构钩子红被归因为取证并发并整轮重跑。这 4 条不由执行者自行判定成立。
