# rv-1 呈递：快速通道真实生效（加旗标 run 立即开 PR、无验证阶段、PR 带未验证标注）

分支: `issue-207` @ `f44a3b4f`
真实入口: 进程内调用真实 `run_once(..., fast_merge=...)` 执行循环 + 发布链路（GitHub 客户端与 agent 子进程按 PRD `mock_boundary` 打桩；旁路判定、Phase 4.5 门禁、PR 正文注入均为真实代码）。

## 完整终端输出

```text
RV1 NEGATIVE-CONTROL (default run, no flag): pr_count=1 marker=None skip_logs=0 -> OK-red-baseline
RV1 GREEN (fast-merge run): pr_count=1 marker_issued=123 human_notice=True skip_log_count=2 both_gates_named=True
RV1 FRESH-STATE-PROBE (issue #124 rerun): marker_issued=124 skip_log_count=2
RV1 PR-BODY-TAIL:
  |
  | <!-- iar:fast-merge issued=123 -->
  |
  | > **快速通道发布**：本 PR 经快速通道发布，未经过自动化验证门禁，合并前请人工验证。
RV1 VERDICT: PASS
```

## PR 正文渲染（快速通道 run 开出的 Draft PR 末尾）

> **快速通道发布**：本 PR 经快速通道发布，未经过自动化验证门禁，合并前请人工验证。

机器可读标记（HTML 注释，渲染不可见、程序可解析）：`<!-- iar:fast-merge issued=123 -->`

## 10 秒自检

- 在 GREEN run 的两条 Phase 4.5 审计日志中确认点名 `rv_reexec` 与 `verifier` 两个被跳过阶段（`skip_log_count=2 both_gates_named=True`）；默认 run 无此类日志（`skip_logs=0`）。
- PR 正文中搜 `iar:fast-merge` 命中，且 `issued=` 数字随 Issue 号变化（#123→123、#124→124，证明非陈旧缓存）。
- 反向对照：去掉旗标的默认 run（NEGATIVE-CONTROL）`marker=None`，验证阶段照常。

## 复核命令（原始输出，本地 gitignored）

```bash
uv run python .iar/evidence/scripts/rv1_fast_merge_real_run.py
# 原始 stdout 快照: .iar/evidence/rv-1-fast-merge-run.txt
```
