# Verification Plan — P1-FEAT-20260916-023404-iar-prd-skill-alignment

对应 PRD：`tasks/pending/P1-FEAT-20260916-023404-iar-prd-skill-alignment.md` §7.6 Realistic Validation Plan。
每条 oracle 的执行命令与证据文件命名如下，证据按 `rv-<n>-<slug>.<ext>` 命名存放于本目录。

## rv-1 — init gitignore 白名单 provisioning（reviewer: human, R1）

- 命令：`bash tasks/evidence/P1-FEAT-20260916-023404-iar-prd-skill-alignment/scripts/capture_rv-1-init-gitignore.sh`
- 脚本行为：`mktemp -d` 建临时 git 仓库，用 keda 当前 venv 的 `iar init` 真实 CLI 初始化（skill 拉取走 `--dry-run` 断言计划，避免网络依赖；注意避免污染全局 registry——若 init 写全局 config.toml，脚本结束后清理该条），断言 `.gitignore` 含 `tasks/evidence/**` 与 `!tasks/evidence/**/*.md` 且在 iar 托管块内；重跑一次断言幂等。
- 证据：`rv-1-init-gitignore.txt`（临时仓库 .gitignore 全文 + 两次 init 终端输出）
- 负向控制：脚本前半先断言旧规则不存在于临时仓库（fresh state）。

## rv-2 — prompt 瘦身 golden 测试（reviewer: verifier, R2）

- 命令：`uv run pytest tests/ -k prompt_contract -v`
- 新增测试直接调用真实 prompt 构建函数（`build_prompt` / `build_recovery_prompt` / `build_progress_continuation_prompt` / `build_closeout_prompt`），断言：格式教学片段（六字段示例、复选框语法教学、rv 命名教学）零命中；契约指针行与 runner 私有语义块各恰好一次命中。
- 证据：`rv-2-prompt-contract.txt`
- 负向控制：临时删除指针行后测试必须红（在证据中记录该红跑输出片段）。

## rv-3 — 证据提交语义集成测试（reviewer: verifier, R2）

- 命令：`uv run pytest tests/ -k evidence_commit_semantics -v`
- 集成测试：`tmp_path` 真实 git 仓库，证据写入 `tasks/evidence/<prd-stem>/`（含 `.md` 报告与 `.png` 原始产物），走真实 `git add -A` + commit，用 `git ls-tree` 断言 commit 树只含 `.md`；强制 `git add -f` 加入 `.png` 后 `ensure_no_evidence_paths_in_changes` 必须拒绝；显式 `evidence_dir=".iar/evidence"` 的 legacy 用例行为不变。
- 证据：`rv-3-evidence-commit.txt`（测试输出 + `git ls-tree` 清单）
- 负向控制：临时移除临时仓库 .gitignore 白名单重跑，`.png` 必须出现在 commit 树中（证明测试能区分失败），记录该输出。

## rv-4 — 预检 fail fast（reviewer: verifier, R1）

- 命令：`uv run pytest tests/ -k skill_preflight -v`
- 用 env 覆盖（`IAR_PRD_SKILL_PATH`）分别指向：缺失路径、含 `Machine-Contract-Version: 0` 的 fixture、正常 skill；断言前两者 fail fast 且报错含 `iar init` 指引，第三者放行。
- 证据：`rv-4-skill-preflight.txt`

## rv-5 — 全量回归（reviewer: verifier, R1）

- 命令：`uv run pytest tests/ -x -q`（若全量过慢，先 `-k agent` 再全量）
- 证据：`rv-5-full-regression.txt`

## rv-6 — skill 契约一致性（reviewer: verifier, R1）

- 命令：`bash tasks/evidence/P1-FEAT-20260916-023404-iar-prd-skill-alignment/scripts/capture_rv-6-contract-sync.sh`
- 脚本行为：解析 `~/code/zata_code_template/skills/prd/SKILL.md` 的 Machine Contract 章节与版本标记；grep keda 侧 `SUPPORTED_MACHINE_CONTRACT_VERSION` 常量并比对相等；`diff` 模板仓库与 `~/.kimi-code/skills/prd/` 副本逐字节一致；断言章节含六项约定关键词（Change Log 六字段、`[~]`、rv-id 命名、证据目录布局、Delivery Dependencies、横幅标记）。
- 证据：`rv-6-contract-sync.txt`
