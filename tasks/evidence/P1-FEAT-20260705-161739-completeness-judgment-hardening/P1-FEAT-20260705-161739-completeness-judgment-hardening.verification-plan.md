# 验证计划 · iar 完成度判定结构性盲区加固

PRD：`tasks/pending/P1-FEAT-20260705-161739-completeness-judgment-hardening.md`
分支：`feat/completeness-judgment-hardening`（worktree `../keda-worktrees/feat/completeness-judgment-hardening`）
实施时间：2026-09-16

## 验收项 → 可执行验证映射

| 验收项 | 验证方式 | 命令 / 入口 | 证据文件 |
|---|---|---|---|
| rv-1 假绿灯抓取（FR-2） | 真实入口：生产 `IProcessRunner`（SubprocessRunner + 协议路由）真跑 `bash -lc`，manifest 解析器真读盘 | `uv run python /tmp/iar_rv_oracle.py`（临时 worktree `git init` + `.iar/evidence/evidence.json`） | `rv-1-rv-2.txt` |
| rv-2 旧 manifest 兼容（FR-1） | 同上，另跑非法 JSON 负控 | 同上 | `rv-1-rv-2.txt` |
| rv-4 大 PR 关键文件全量（FR-6 / FR-7） | integration：构造 15 文件 / 约 13650 字符 diff，`build_supervisor_prompt` 真跑 | `uv run pytest -o addopts="" tests/test_pr_supervisor.py -k "layered or key_paths"` | `rv-4.txt` |
| rv-5 跨 cycle finding 累积（FR-8 / FR-9 / FR-10 / FR-12） | integration：artifact 读写真跑（tmp_path 落盘），supervisor LLM mock；另跑 cycle1→cycle2 端到端 | `uv run pytest -o addopts="" tests/test_pr_supervisor.py -k "previous or finding" tests/test_agent_runner_supervisor_entrypoints.py -k cycle_2_prompt` | `rv-5.txt` |
| rv-7 现有测试不回归 | 全量 pytest | `uv run pytest -o addopts="" tests/ -q` | `rv-7.txt` |
| 漂移守卫（三处同步 / 字段名 / 截断唯一落点） | grep | `rg -n "key_paths\|max_diff_chars\|..." src/backend ...` | `drift-guard.txt` |
| 架构门禁 | `check_architecture.py` | `uv run python hooks/shared/check_architecture.py` | `arch.txt` |
| finding artifact 被 gitignore 排除 | `git check-ignore` | `git check-ignore -v .iar/state/issue-1/findings.json` | `gitignore-check.txt` |

## Mock 边界

- rv-1 / rv-2：**无 mock**。命令经 `bash -lc` 真执行，manifest 真读盘解析；仅用 `IssueSummary` 值对象替代 GitHub（本验证不涉及 Issue/PR 交互）。
- rv-4 / rv-5：`IProcessRunner` 用测试替身返回构造的 diff（git diff 不是被测对象），supervisor LLM 用替身返回 JSON 决策；**artifact 读写、prompt 构造、断言逻辑全部真跑**。
- rv-6（真实 GitHub issue 端到端）：需要 GitHub token，标 opt-in，本次不执行（`required_for_acceptance: false`）。

## 负控设计

| Oracle | 负控 | 期望差异 |
|---|---|---|
| rv-1 | 删掉 manifest 的 `stdout_assertions`，其余输入不变 | 加断言 → `ValidationEvidenceError`；删断言 → 通过（证明判别力来自断言，不是退出码） |
| rv-2 | `evidence.json` 写成非法 JSON | 合法 → 通过；非法 → 报错（证明解析器真在跑） |
| rv-4 | `key_paths=()`、`max_diff_chars=2000` | 配关键路径 → 关键文件全量可见；不配 → 同一文件被截断掉 |
| rv-5 | 删掉 `.iar/state/issue-<N>/findings.json` | 有 artifact → prompt 含 `Previous unresolved findings`；无 → 不含 |
| rv-7 | 把 `must_match` 判断写反 | 写反 → `test_ensure_validation_commands_pass_enforces_stdout_substring` 失败（对抗自检，见证据报告） |

## 对抗自检（实施期执行）

- `must_match` 判断取反 → 目标测试必须失败（证明测试有判别力）。
- 本次以"断言失败不写缓存"的额外断言补强：假绿灯若被缓存固化会造成二次污染。
