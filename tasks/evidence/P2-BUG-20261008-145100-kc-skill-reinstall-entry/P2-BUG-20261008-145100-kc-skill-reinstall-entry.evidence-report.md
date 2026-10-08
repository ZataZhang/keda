# Evidence Report — P2-BUG-20261008-145100-kc-skill-reinstall-entry

## 人审导航 / Human Review Navigation

给人看的只有两行（对应 PRD §9.1 呈递表）；机器全量证据在本文后半部分。

| Oracle | 你要看什么 | 呈递物（仓库相对路径 + 打开方式） | 预期内容 | 交叉核对 |
|---|---|---|---|---|
| rv-3（§2 唯一人工决策的物证） | 本地改动过的 `prd` skill 在无 `--force` 时不被覆盖 | `tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-conflict.txt`；`open "tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-conflict.txt"` | 输出含 `Refusing to overwrite user-owned skill 'prd'` 与具体路径，脚本记录的退出码非零（1） | 同目录 `rv-3-force.txt` 确认 `--force` 后退出码 0 且用户内容被替换；负控测试 `tests/test_cli_skill_install.py::test_skill_install_remote_template_failure_exits_one_like_init` 锁死与 `kc init` 同码 |
| rv-1（新入口端到端） | 已有 `.kedacode.toml` 的仓库里新命令装齐全部安装根 | `tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-1-install.txt` 与 `rv-1-init-failure.txt`；`open` 同上换文件名 | `rv-1-install.txt` 列出 4 个安装根各一行 `Installed KedaCode operator skill`，并含旧名清理/保留两条回显；`rv-1-init-failure.txt` 含 init 报错与指向 `kc skill install` 的双语提示 | 配置字节不变由脚本 sha256 前后比对断言（PASS 行在捕获件内）；`just prd review tasks/pending/P2-BUG-20261008-145100-kc-skill-reinstall-entry.md` 可打开完整检查单 |

唯一待人工确认项：PRD §9.2 Human-Confirmed 第 1 项（`kc skill install` 对两类远程模板失败统一返回退出码 1 是否可接受），回复方式见同目录 `human-review-checklist.md`。

## 机器证据索引 / Machine Evidence Index

### 真实入口 RV 捕获件（隔离 HOME 沙箱，脚本幂等可复跑）

执行脚本：`scripts/rv-skill-install.sh`（本目录下，不入代码 diff）。沙箱约定：HOME 指向 `mktemp` 临时目录，安装根走真实探测（4 根），远程模板 `prd`/`code-reviewer` 走真实 git clone。

| rv-id | 捕获件 | 关键断言结果 |
|---|---|---|
| rv-1 | `rv-1-init-failure.txt` | `kc init` 在已有 `.kedacode.toml` 下非零退出，输出含 `kc skill install` 指引（PASS） |
| rv-1 | `rv-1-install.txt` | `kc skill install` 退出码 0；4 个安装根全部出现 `kedacode-operator/SKILL.md`；目标仓库 `.kedacode.toml` sha256 前后不变；`Removed the legacy operator skill copy at …/.kedacode/skills/iar-operator` 与 `Kept the modified legacy operator skill copy at …/.codex/skills/iar-operator` 两条回显均在（PASS） |
| rv-1 | `rv-1-idempotent.txt` | 复跑退出码 0，输出含 `already up to date`（PASS） |
| rv-2 | 并入 `rv-1-install.txt` | 原样旧名副本删除、改动副本保留且字节不变、两分支不串（check_not 通过）（PASS） |
| rv-3 | `rv-3-conflict.txt` | 冲突时非零退出（1）、输出指名 `prd` 路径、盘上用户内容不变（PASS） |
| rv-3 | `rv-3-force.txt` | `--force` 退出码 0、用户内容被远程模板替换、改动过的旧名副本同时删除（PASS） |
| rv-4 | `rv-4-schema.json` | `kc schema --json` 的 `commands` 含 `["skill","install"]`（PASS） |
| rv-4 | `rv-4-dryrun.txt` | dry-run 退出码 0、安装根 sha256 树前后一致（零写入）、输出含 `Would install`（PASS） |
| rv-4 | `rv-4-repo-selector.txt` | `--repo <仓库>` 退出码 2、输出含 `takes no repository`、目标配置不变（PASS） |

种子文件：`scripts/legacy-iar-operator-SKILL.txt`（历史随包 `iar-operator/SKILL.md` 原样字节留档，取自 `git show b77e74d8:src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md`，仅作复跑种子，gitignore 不入 diff）。

### 测试层证据

- `uv run pytest -o addopts="" tests/test_cli_skill_install.py tests/test_agent_runner_init.py tests/test_kedacode_operator_skill.py` → **77 passed**（9 个新 CLI 测试覆盖四条验收口径 + 退出码一致性 + schema 收录 + selector 拒绝 + init 指路；既有 init/skill 测试重打桩后全绿）。
- `uv run pytest -o addopts="" tests/test_remote_template_skills.py tests/test_cli_schema.py` → **23 passed**。
- 负控：`test_skill_install_remote_template_failure_exits_one_like_init` 断言 `kc skill install` 与 `kc init` 对远程模板失败同以退出码 1 失败。

### 全量门禁证据

- `CI=true JUST_FULL_TEST_FLAGS="--no-testmon" just test all` → **3667 passed, 1 skipped**（196.56s，全量非 testmon 增量）。
- `SKIP=check-test-flag uv run pre-commit run --all-files` → 全部 Passed（ruff / ruff-format / 架构层依赖 / 文件行数 / check-prd-acceptance-checklist 等）。
- `uv run mkdocs build --strict` → 通过（`docs/guides/agent-runner.md` 新增 `kc skill install` 章节）。
- `uv run python ~/.claude/skills/prd/scripts/check_prd_acceptance_checklist.py --repo-root . --check-provided --archive-ready tasks/pending/P2-BUG-20261008-145100-kc-skill-reinstall-entry.md` → **PASS**。

### 偏差与残余风险（如实披露）

- 离线场景：远程模板克隆失败会阻断整条命令（含不需要网络的随包安装）——与 `kc init` 一致的既有耦合，PRD §6/§12 已记录，不在本 PRD 解耦。
- 远程原生 subprocess 失败（非 `RemoteTemplateSkillInstallError`）走顶层通用错误封套而非「Remote template skill installation failed」前缀行——退出码仍非零、信息完整，属消息形态差异，PRD §12 已记录。
- 代码评审 4 处打磨建议（Facade `__all__` 补 `skill_app`、随包 skill Recovery 补 fail-closed 说明、selector 守卫与测试 helper 抽取、`cli_typer_skill` 换用 `_run_typer_repository_command`）评估为既有模式一致性取舍，不静默扩大改动面，记录备查。
