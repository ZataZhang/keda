# 验证计划：产品表面改名为 KedaCode / `kc`

> 本文件是 `tasks/pending/P1-REFACTOR-20261007-013512-rename-product-surface-to-single-new-name.md`
> §7.6「Realistic Validation Plan」的执行副本。**判据以 PRD §7.6 为唯一事实源**，
> 本文件只记录「怎么跑、跑在哪棵树上、结果落在哪个文件」。
> 人读入口是同目录的 `….evidence-report.md`（首节「人审导航」）。

## 复现环境

| 项 | 值 |
|---|---|
| worktree | `/Users/zata/code/keda/.iar-worktrees/issue-228` |
| 分支 | `issue-228` |
| base commit | `36c3f734`（`git merge-base HEAD main`），基线代码树 `8ff493493a919235de313de1c495ff4400a4f5a7` |
| 被测代码树 | `h8ff493493a919235de313de1c495ff4400a4f5a7+d8b0e85046983c6faabd2cab55f0de66c6ea10d00+u0e855cf868906ccea532dee5cf00978c15cc24cb`（`h<HEAD^{tree}> + d<非 `tasks/` 已跟踪改动整段 diff 的 blob hash> + `u<非 `tasks/` 未跟踪代码文件的 blob 指纹>`；241 个已跟踪改动文件 + 9 个未跟踪代码文件）。`git stash create` 的 tree 仅在各报告头作参考行，不作为绑定口径 |
| 指纹口径为何换掉 | 旧口径 = `git stash create` 的 tree + 未跟踪代码文件指纹，前半段把 `tasks/**`（PRD 正文、本计划、证据报告）算进去了：实测只改 `tasks/pending/` 里这篇 PRD，前缀就从 `5c14f59c…` 跳到 `99f72b57…`，等于「证据绑定最终代码树」永远达不成，还会让 rv-10 的门禁后自检把「我在写文档」误报成「门禁改动了工作树」。交付记录不影响任何 oracle，不该进代码树指纹。理由与影响见 PRD Change Log「代码树指纹改为与交付记录无关」 |
| 指纹移位与收敛 | 第一次真实移位来自门禁的自动修复：`SKIP=check-test-flag just lint --full`（pre-commit `ruff --fix` + `ruff-format`）就地改写了未跟踪的新增测试 `tests/test_state_home_migration.py`，未跟踪段从 `…ue2ece0e2…` 移到 `…u0e855cf86…`（改动是纯排版，语义无变化）。对最终树再跑 `ruff` 与 `ruff-format` 是空操作、指纹不变，即已收敛；rv-10 的门禁后自检每次都用同一条命令回算并与报告头逐字节比对，不一致整份作废。十条 oracle 的正向证据现在全部绑在上表这一个 id 上：rv-1、rv-4、rv-5、rv-7 于 22:00–22:01，rv-2 / rv-9 于 22:28–22:29，rv-3 / rv-6 / rv-8 于 22:36，rv-8 附项的契约 diff 于 22:47 生成，rv-10 门禁于 22:43 单独重跑；rv-9、rv-10 负控与其余基线负控于 22:33–22:36 按最终镜像脚本重收。rv-10 在 22:32 那一轮整份作废：它的第 2 步 `just lint --full` 把我的并发文档写入误归给 `check-architecture` 钩子而报红（钩子自身只检查、不写文件），重跑时期间零改动工作树，六步退出码全 0 且门禁后指纹与报告头一致。第二次移位来自 runner 打回后的修复：`.iar.toml` 的 `agent_runner.validation.evidence_dir` 从被误改的 `tasks/evidence` 回退成本仓既有的 legacy `.iar/evidence`——这是已跟踪文件，所以已跟踪段从 `…da60ff35…` 移到 `…d8b0e850…`；十条正向、九条基线负控与六步门禁全部在这棵新树上重收（2026-10-08 01:11–01:22，逐步耗时见 `.iar/evidence/_run-status.log`），报告头与门禁前后自检都按新 id 逐字节比对 |
| 原始证据与 RV 脚本 | `.iar/evidence/`，**RV 脚本的唯一归宿是 `.iar/evidence/scripts/`**。本仓 `.iar.toml` 把 `agent_runner.validation.evidence_dir` 显式配成 legacy `.iar/evidence`，该目录由 `.git/info/exclude` 排除，是 worktree 本地文件，不进代码 diff |
| 呈递副本 | 本目录只放**证据产物**的副本：同名 `.txt`、门禁六步 `rv-10-gates-step1..6.log`、平铺的 14 组成对截图与各自的同名页面文本侧车（PRD §7.6 / §9.1 第 2 行指定的呈递位置）；只有 `*.md` 入库。**本目录不放 RV 脚本，也没有 `scripts/` 子目录**——上一轮把 23 个脚本镜像到这里，被交付门整体打回：`expand_changed_path()` 会把未跟踪目录 rglob 展开成逐文件条目，`tasks/evidence/**` 的 gitignore 挡不住判定，于是镜像的 `.sh/.py/.mjs` 全部算进代码 diff（见 PRD Change Log「RV 脚本退回证据目录唯一归宿」）。副本由 `.iar/evidence/scripts/sync_presentation_copies.sh` 单向同步，该脚本只搬 `rv-*.txt` / `rv-*.png` / `rv-10-gates-step*.log`，遇到任何 `.sh` / `.py` / `.mjs` / `.js` 直接拒绝，并删除本目录残留的 `scripts/`；本轮同步 55 个产物、清理 0 个脚本条目 |
| 结构化证据清单 | `.iar/evidence/evidence.json`（manifest 落在 `evidence_dir` 根，即 legacy 口径的平铺目录），由 `make_manifest.py` 生成；10 条命令一律走 `.iar/evidence/scripts/`，rv-3 / rv-7 / rv-8 的命令用 `ensure-baseline-export.sh` 自建 merge-base 基线导出。`evidence_files` 只写裸文件名（runner 相对 `evidence_dir` 解析）。RV 脚本对兄弟脚本（种子、快照生成器、守卫、指纹）的引用一律按 `SCRIPT_DIR` 自身位置解析，所以从证据目录复跑与从别处复跑等价 |
| 编排与判定口径 | 十条 RV 串行跑，rv-9 的 `just console-sync` 与 rv-10 的 `pnpm … typecheck` / `build` 绝不重叠（22:23 那轮 rv-9 就是被并发前端构建的 `.next` 锁挡住的）。**rv-10 的门禁期间对工作树零写入**：pre-commit 会把钩子执行窗口内观察到的任何改动归给该钩子，22:32 那轮我边跑 lint 边追加 Change Log，`check-architecture` 因此误报「files were modified by this hook」，隔离重跑即全绿（见 PRD Change Log「rv-10 的 lint 红来自取证并发」）。编排层不采信进程退出码：macOS bash 3.2 在 `set -u` 下遇到 `$VAR（` 这种形态会中止脚本却仍返回 0，因此每条 RV 一律回读它自己的证据文件核对终局标记（绿要 `^PASS: rv-<n>`，红要 `^FAIL`），标记与行数一起记进 `.iar/evidence/_run-status.log` |
| 复跑预算 | runner 对每条 manifest 命令复跑上限 300 秒（`validation.reexecute_timeout_seconds`）。item 10 的复跑命令只含「残留守卫 + 四个直接相关测试文件 + 只读隔离探针」（3–5 秒）；六项全量门禁（全量 pytest 单独 169.71 秒）作为已录制证据保留在 `rv-10-gates.txt` + `rv-10-gates-step1..6.log`，并在该 item 的 `risks` 里披露，见 PRD Change Log「rv-10 复跑命令的预算披露」。每条 item 的 `evidence_files` 里的基线负控文件由负控命令产出，不串进复跑命令（串了必超预算），这一点写在各自 item 的 `risks` 与 `negative_control` 里；除此之外列出的产物必须能由该 item 自己的命令复现——本轮据此补了两处：item 8 的命令在 `rv-8-distribution.sh` 之后串上 `rv-8-contract-diff.sh`（两者合计 5 秒，不挤占预算），item 10 的命令末尾用 `set -o pipefail` + `tee` 落 `rv-10-isolation-probe.txt`（`pipefail` 是必需的：不加的话探针判红也会被 `tee` 的退出码 0 洗成绿） |
| 已安装对象 | 每条 RV 自己 `uv build --wheel` 出的 wheel，经 `uv tool install` 装进临时 `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR`，在临时 `HOME` 下调用真实可执行文件；**全程不用 `uv run` 或 `python -m` 代替产品命令**（`uv run pytest` 只用于单元层断言） |
| wheel 指纹 | 各报告头单独记录（每条 RV 各自重建；未改动内容时 `uv build` 可复现出同一 sha256，不同 RV 之间相同或不同都不构成信息）：rv-1 `74cb0fdd…`、rv-2 `d0c3ea29…`、rv-4 `05c966ea…`、rv-5 `e37dcd87…`、rv-6 `ed724e56…`、rv-7 `0b033bb3…`、rv-8 `2143d3be…`、rv-9 `0a257524…` |
| 浏览器 | 本机没有 Playwright，rv-9 用系统 Google Chrome `--headless=new` 经 CDP 驱动（`rv-9-browser-driver.mjs`，1440x900），页面与接口都是真实的，只有驱动层换实现。驱动对连接与普通 CDP 调用设 30 秒上限、`Page.navigate` 90 秒，页面与文档抓取各重试两次，结束时按本次独有的 `--user-data-dir` 回收浏览器进程；`rv-9-ui-copy.sh` 在浏览器之前用 curl 逐 URL 记录状态码与 `time_total`，驱动失败时把 console 输出尾部一并落盘。**驱动 node 进程用真实 HOME**（报告头 `browser_driver_home` 行同时记驱动 HOME 与产品沙箱 HOME）：对照实验证明伪造 HOME 会让 Chrome 的 `Page.navigate` 永不回响应而服务端 1.8ms 就返回 200，被测 console / 已安装 CLI / 临时仓库 / 数据库仍在临时 HOME 沙箱内不变，见 PRD Change Log「rv-9 浏览器驱动改用真实 HOME」 |

隔离骨架（rv-1、rv-2、rv-4 至 rv-9 共用）除 `HOME` / `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` 外还做四件事，
理由见 PRD Change Log「隔离环境补强」：清除继承来的 `IAR_*` / `KEDACODE_*`；固定
`UV_CACHE_DIR` / `UV_PYTHON_INSTALL_DIR`；所有 CLI 调用都在仓库外的空目录 `NEUTRAL_CWD`；产物路径统一 `pwd -P`。
每条 RV 的断言里都含「registry 条目路径必须全部落在临时目录内」的包含性检查。

## Oracle 与执行入口

| id | 判据（摘要） | 实际执行 | 证据文件 |
|---|---|---|---|
| rv-1 | 三入口行为一致、提醒只跟 `iar`、全新机器只生成 `~/.kedacode` | wheel → `uv tool install` → 临时 HOME 下用 `kc`/`kedacode`/`iar` 真实可执行文件跑 `--version`、`schema --json`、`registry list --json`、`registry list`，逐字节比 stdout 与退出码，按命令统计 stderr 提醒次数；结束后由新 shell 列临时 HOME 顶层条目 | `rv-1-entrypoint-parity.txt` |
| rv-2 | 只有旧目录的机器照常可用；`config migrate` 的拒绝/预演/迁移/幂等/双目录规则 | 在只含 `.iar` 的临时 HOME + 已登记临时仓库 demo 上，依次真实执行 `registry list --json` → 起真实 `kc console` 经 HTTP 读运行记录 → 用 `kc`/`kedacode`/`iar` 命名的真实桩进程 + 真实锁文件造「占用」→ `config migrate` → `--dry-run` → `config migrate` → `ls -la`/`readlink` → 新起 console 复读 → 再次 migrate → 新旧两个独立真实目录并存 | `rv-2-migrate-lifecycle.txt` |
| rv-3 | 标记逐字节兼容，旧版本未过期认领被判为已占用 | 同一快照脚本（`rv-3-marker-snapshot.py`，按 `SCRIPT_DIR` 与 rv-3 同目录解析）在基线树与最终树各跑一次（都是真实写入函数），剥掉 provenance 字段后逐字段 diff；`uv run pytest -o addopts= tests/test_marker_compat_replay.py` 回放。报告头另记 `tree:` 绑定指纹与 `stash_tree` 参考行 | `rv-3-marker-compat.txt` |
| rv-4 | 环境变量与仓库配置新名优先、旧名兜底、冲突警告、机器模式静音、子进程双注入 | 临时 HOME 两份登记不同 repo id 的 config，四次真实 `kc registry list --json`（只设 `IAR_CONFIG` / 只设 `KEDACODE_CONFIG` / 两者异文件 / 两者同文件），断言 stdout 可解析且 repo 来源正确、stderr 提示次数；`uv run pytest -o addopts= tests/test_product_identity.py` | `rv-4-env-precedence.txt` |
| rv-5 | 三种自有名字都被识别、自我调用优先 `kc`、REPL 前缀、`SubprocessRunner` 换名 | 临时 HOME 登记 demo 后起三个真实桩进程（文件名 `kc`/`kedacode`/`iar`），已安装 `kc registry list --json` 断言 `running` + `unmanaged_count=3`，并用 `ps` 独立佐证；四个测试文件 `uv run pytest` | `rv-5-own-process-recognition.txt` |
| rv-6 | 新旧插件分组都发现、同 id 新分组优先、内置协议不重复 | 临时目录构建两个最小插件分发包（分别注册 `iar.agent_output_protocols` 与 `kedacode.agent_output_protocols`），`uv tool install --with` 真实安装后跑 `kc agent doctor --protocols --json`；冲突优先级由 `tests/test_output_protocol_registry.py` 断言 | `rv-6-plugin-groups.txt` |
| rv-7 | `kc init` 装 `kedacode-operator`，原样旧副本清理、改动副本保留并提示 | 临时 HOME + `KEDACODE_SKILLS_DIR`，在两个用户 skill 根预置取自 git 历史的 `iar-operator` 原样副本与改动副本，真实执行 `kc init`；`uv run pytest -o addopts= tests/test_kedacode_operator_skill.py` 做命令示例漂移检查 | `rv-7-operator-skill.txt` |
| rv-8 | 分发与周边产物跟随新名 | 解包 wheel 读 `entry_points.txt`；`kc completion install --shell zsh` 后查 `_kc`；`bash install.sh --check`；已登记临时仓库上 `kc container up … --dry-run` 读 Env overrides；读 `Dockerfile.runner` 与 `release.yml` 骨架。附项 `rv-8-contract-diff.sh` 另比 CLI 机器可读契约：基线导出与最终树各跑一次 `uv run <bin> schema --json`，只比命令路径、参数/选项元数据与退出码枚举（`help`/`example` 单列计数不参与判定），并自带比对器负控（人为删掉 `init` 的一个选项必须报红）。GitHub 侧项见「不在本地验证范围」 | `rv-8-distribution.txt` + `rv-8-contract-diff-cli-surface-exitcodes.txt` |
| rv-9 | 管理终端与迁移文档用户可见名为 KedaCode / `kc` | 真实 `just console-sync` 构建前端 → 重建安装 wheel → 临时 HOME 起真实 `kc console`（空闲端口）→ Chrome/CDP（驱动进程用真实 HOME，被测 console 仍在临时 HOME 沙箱）打开首页、dashboard、设置、统计空态、仓库页（触发扫描、未找到提示、目录选择徽标）并截图；`uv run mkdocs build --strict` 后打开 `site/guides/migrating-from-iar/`（迁移文档页按设计保留旧名对照，`bare_iar=26` 不参与零残留判定） | `rv-9-ui-copy.txt` + `rv-9-*.png`（实现）/ `rv-9-baseline-*.png`（基线） |
| rv-10 | 旧名残留为零 + 全量回归 | `bash .iar/evidence/scripts/rv-10-residue-guard.sh`（G1–G8）、`SKIP=check-test-flag just lint --full`、`uv run pytest -o addopts= -q`、`uv run mkdocs build --strict`、`pnpm --dir frontend-public typecheck`、`pnpm --dir frontend-public build`，逐条记录退出码并把每步完整输出落 `rv-10-gates-step<N>.log`；跑完再算一次代码树指纹，与报告头不一致则整份作废；末尾追加只读的 `rv-10-isolation-probe.sh`（隔离环境不变量，毫秒级，不挤占 300 秒复跑预算） | `rv-10-gates.txt` + `rv-10-gates-step1..6.log` + `rv-10-isolation-probe.txt` |

## 负控（每条 oracle 先证明会红）

同一条脚本改用 `git merge-base` 基线树执行，结果记在 `rv-<n>-negative-control-baseline.txt`
（唯一例外是 rv-8 附项：它的负控在同一棵最终树上人为造一个已知差异，用来证明比对器不是恒真）：

| id | 基线红在哪 |
|---|---|
| rv-1 | `FAIL: 安装后缺少可执行文件: kc`（基线只有 `iar`/`kedacode`） |
| rv-2 | 同上，基线没有 `kc` 入口 |
| rv-3 | 在临时目录里把 fixture 副本的 `iar:claim` 改成 `kedacode:claim` 后跑回放用例，退出码 1（未过期认领不再被识别为已占用）；另含基线树/最终树两栏 tree id |
| rv-4 | 基线没有 `kc` 可执行文件 |
| rv-5 | 基线没有 `kc` 可执行文件 |
| rv-6 | 基线没有 `kc` 可执行文件 |
| rv-7 | 基线没有 `kc` 可执行文件 |
| rv-8 | `entry_points.txt` 的 console_scripts 里缺 `kc = backend.api.cli:main` |
| rv-8 附项（比对器自证，不是基线树） | 在最终侧 `schema --json` 里人为删掉 `init` 的一个真实选项（`--no-update-gitignore`，7 → 6），比对器必须退出码 1 并输出 `RESULT: DIFF`；否则第 2 段的 SAME 可能是恒真 |
| rv-9 | 走到文案断言才红（不是死在驱动）：标题 `iar — Agent Runner 管理终端`、root/app/repositories 独立 `iar` 计数 1、stats 2、settings 4，缺 `关于 KedaCode 管理终端`/`.kedacode.toml`/`kc console`/`kc run`/`扫描本地 KedaCode 仓库`。负控带 `--skip-interactions`，故 `repositories-scan` / `-notfound` / `-picker` 三个交互截图点记为「缺少该截图点」，这是负控的既定范围，不是缺陷 |
| rv-10 | 残留守卫在基线树按预期变红（退出码 1，见 `rv-10-negative-control-baseline.txt`） |

rv-9 基线负控需要一处 setup 补偿并在证据里注明：基线版本没有「全局安装在状态目录补一份最小 registry 载体」
的修复（PRD Change Log「全局安装缺少 registry 载体的缺陷修复」正是改名暴露的既有问题），`iar registry sync` 会以退出码 3 失败而停在
setup。脚本因此在基线分支预写一份最小配置并用旧名最高优先级的 `IAR_CONFIG` 指过去，让负控走到文案断言。

## 不在本地验证范围

- **PR 上 `install-smoke` 工作流**（rv-8 与 Validation Acceptance 的一条）：结论只能来自 GitHub Actions，
  需 runner 发布 PR 之后读取，本地无法产出。
- **Homebrew tap 发布与 PyPI 发布**：外部仓库 / 需授权动作，PRD §11 已列为非目标，只验证仓库内骨架。
- 真实 `~/.iar` / `~/.kedacode` 全程未被读写。该前提不再是散文声明：`rv-10-isolation-probe.txt` 逐脚本断言 8 个驱动已打包 CLI 的 RV 脚本都有 `export HOME=`（并如实列出 3 个不建 HOME 沙箱的脚本及理由），结果层给出宿主 `~/.iar` 在取证窗口 21:40–23:00 内改动条目数为 0（dir mtime 20:03:12，早于窗口）且宿主上不存在 `~/.kedacode`；探针自带 `--negative-selftest` 会在窗口内被写入的目录上判红。见 PRD Change Log「逐图三件套与隔离探针」。
- 真实 daemon 未被停止：所有占用场景都用临时 HOME 沙箱里的桩进程与桩锁文件（`rv-2-migrate-lifecycle.txt` 的「占用场景」段），迁移命令按家目录作用域扫描，不会把宿主 daemon 判成占用、更不会动它。
