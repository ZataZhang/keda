# Verification Plan — P2-BUG-20261008-145100-kc-skill-reinstall-entry

本文件是 `tasks/pending/P2-BUG-20261008-145100-kc-skill-reinstall-entry.md` §7.6 Realistic Validation Plan 的可执行计划落稿：每条 oracle 的真实入口、步骤、预期与证据捕获件。执行环境：macOS 本地，worktree `/Users/zata/code/keda/.iar-worktrees/issue-245`，`uv run --project <worktree> kc` 真实 CLI 入口。

## 沙箱约定

- 单脚本执行：`tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/scripts/rv-skill-install.sh`（`bash scripts/rv-skill-install.sh`，幂等、自足，只写 `mktemp` 沙箱）。
- 隔离 HOME：`SANDBOX_ROOT=$(mktemp -d /tmp/kc-skill-rv.XXXXXX)`，`export HOME=$SANDBOX_ROOT/home`，安装根走真实探测（不设 `KEDACODE_SKILLS_DIR`），预期解析出 4 个根：`~/.kedacode/skills`、`~/.codex/skills`、`~/.claude/skills`、`~/.codebuddy/skills`。
- 目标仓库：沙箱内 `git init` + 预置内容与生成结果不同的 `.kedacode.toml`（模拟「已经 kc init 过」）。
- 远程模板 skill（`prd`/`code-reviewer`）需要网络（git clone 模板仓库）——这是命令的既有前提；无网络时 rv-1/rv-3 如实失败，不按通过处理。

## Oracle 执行计划

| rv-id | 真实入口 | 步骤摘要 | 预期 | 证据捕获件 |
|---|---|---|---|---|
| rv-1 | `kc init` → `kc skill install` ×2（隔离 HOME） | ①已有 `.kedacode.toml` 的仓库跑 `kc init`，断言非零退出且输出含 `kc skill install` 指引；②预置两个旧名副本后跑 `kc skill install`，断言退出码 0、4 根全部装上 `kedacode-operator/SKILL.md`、配置 sha256 前后不变；③复跑断言 `already up to date` | init 被挡且指路；install 全根安装、配置字节不变、复跑幂等 | `rv-1-init-failure.txt`、`rv-1-install.txt`、`rv-1-idempotent.txt` |
| rv-2 | 随 rv-1 第②步 | 在解析出的第 1/第 2 个安装根分别预置：原样旧名副本（`git show b77e74d8:src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md`）与改动过副本；断言前者被删、后者保留，两条回显都在输出中 | 原样旧名副本 Removed 回显；改动副本 Kept 回显且字节不变 | 并入 `rv-1-install.txt`；种子字节留档 `scripts/legacy-iar-operator-SKILL.txt`（不入 git） |
| rv-3 | `kc skill install`（冲突）→ `kc skill install --force` | 实装后把第 1 根的 `prd/SKILL.md` 改写为用户内容：①无 `--force` 断言非零退出、输出含 `Refusing to overwrite user-owned skill 'prd'` 与该路径、盘上用户内容不变；②`--force` 断言退出码 0、内容被替换、改动过的旧名副本同时被删。负控（测试层）：注入 `RemoteTemplateSkillInstallError`，断言 `kc skill install` 与 `kc init` 同以退出码 1 失败 | fail-closed 不回退；两命令同失败同码 | `rv-3-conflict.txt`、`rv-3-force.txt`；`tests/test_cli_skill_install.py::test_skill_install_remote_template_failure_exits_one_like_init` |
| rv-4 | `kc schema --json`、`kc skill install --dry-run`、`kc skill install --repo` | ①schema 解析断言 `commands` 含 `["skill","install"]`；②dry-run 前后对安装根做 sha256 树比对，断言零写入且输出含 `Would install`；③`--repo <仓库>` 断言退出码 2、输出含 `takes no repository`、目标配置不变 | 机器可发现；预览不写；selector 拒绝 | `rv-4-schema.json`、`rv-4-dryrun.txt`、`rv-4-repo-selector.txt` |

## 负向对照

- rv-1 的阶段 1 即负控：`kc init` 在已有配置下仍失败（语义未放宽），失败输出同时把用户导向新命令。
- rv-3 的冲突阶段即负控：fail-closed 未退化为默认覆盖（用户内容仍在盘上）。
- 测试层负控：`test_skill_install_remote_template_failure_exits_one_like_init` 锁死两命令退出码一致性；`test_kedacode_operator_skill.py` 的 flag 白名单锁死新命令的 CLI 表面。

## 全量门禁

- `CI=true just test`（全量，非 testmon 增量）。
- `SKIP=check-test-flag uv run pre-commit run --all-files`（ruff / ruff-format / 架构层依赖 / 文件行数 / PRD checklist 等）。
- `uv run mkdocs build --strict`（文档改动）。
