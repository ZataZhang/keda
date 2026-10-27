# rv-1：认领时快照落盘（真实 `kc run`，真实 GitHub Issue #306）

> 验证层级：**live integration**——真实 `kc run --issue 306`、真实 GitHub Issue、真实 codex 实现 Agent，没有任何替身。比 PRD 计划的"假 gh + 探针 Agent"隔离环境保真度更高（见 PRD 最终对账）。

## 要看什么

认领 Issue #306 之后，本机状态目录里出现该 Issue 的快照文件；其中的正文与认领时 GitHub 上的正文一字不差，代码起点正确。

## 10 秒自验

| 检查项 | 期望 | 实际 |
|---|---|---|
| 快照文件 | `~/.kedacode/dataset/issue-snapshots/keda/issue-306.snapshots.jsonl` 存在 | 存在，**1 行**（整个运行含实现、审核、监督多个阶段之后仍只有 1 行，没有重复；运行过程本身没有逐阶段留档，这一点只能看结果） |
| 正文逐字一致 | 快照 `body` == 认领时 Issue 正文（字节级，不做 strip） | 快照 `body` == 创建 Issue 时保存的原文：**True**；当前 GitHub 正文（REST 接口取回，已用同一份原文还原）== 快照 `body`：**True** |
| SHA-256 | `body_sha256` == 正文的 SHA-256 | **True**（`708f642df71f54b1…`，由独立进程重新计算） |
| schema_version | 1 | **1** |
| 快照字段清单 | 只有任务输入与代码起点，没有评论、没有评判类字段 | `schema_version, captured_at, repo_id, issue_number, title, body, body_sha256, labels, state, url, base_sha, head_sha, base_ref`（共 13 个字段） |
| 标签是认领前读到的 | 不含认领后才加的 `agent/running` | 快照标签 `['type/bug', 'priority/P1']` |
| 代码起点 | 工作目录与基线分支的分叉点 | `base_ref=zata/main`，`base_sha=e17f599a14a2`，`head_sha=e17f599a14a2`（新建工作目录，HEAD 即分叉点，二者相等符合预期） |
| 被处理项目里没有新增文件 | 仓库目录下找不到 `*.snapshots.jsonl` | （无） |

## 关键值的来源

- **认领时的正文**：来自创建 Issue 时保存的正文文件（Issue 创建后、被 runner 认领前没有任何编辑）。认领之后我曾临时编辑过一次，用于验证 `edited_since_claim`（见 rv-4），随后用同一份原文**按字节还原**，并用 REST 接口取回逐字节比较（上表）。
- **快照内容**：来自 runner 落盘的真实文件，不由断言重建。

## 穿越的真实边界

`kc run --issue 306` → 仓库轮询的定向 run → `arbitrate_first_claim`（GitHub 认领评论）→ `create_or_reuse_worktree`（真实 git worktree）→ `record_issue_input_snapshot` → 真实文件追加 → 独立进程读取。

## 局限（如实说明）

- 运行前后的 `git status --porcelain` 当时没有逐次留档；"被处理项目不被弄脏"由上表的文件搜索、快照路径不在仓库内、以及自动化用例 `test_snapshot_does_not_touch_the_processed_repository` 共同支撑。
- 未做 PRD 原计划的"隔离环境 + 假 gh + 探针 Agent"那一组；由本次真实运行取代。
- 认领那一刻的 GitHub 正文无法事后重放；"认领时正文 == 创建时保存的正文"的依据是：创建与认领之间我没有编辑过 Issue，且快照正文与保存的原文字节一致。

## 快照文件原文（完整的一行，已格式化；`body` 为逐字原文）

```json
{
  "schema_version": 1,
  "captured_at": "2026-10-22T06:53:34+00:00",
  "repo_id": "keda",
  "issue_number": 306,
  "title": "[Bug] 带静默超时的捕获型子进程提前返回，丢失最终 stdout（verifier verdict 丢失）",
  "body": "## Summary\n\n### Problem Statement\n\n带 `inactivity_timeout` 的捕获型子进程（独立 verifier 就是这种调用：`capture_output=True`、有 `timeout` 和 `inactivity_timeout`）会**丢失 stdout**：函数在子进程还没结束时就把已读到的输出拼成字符串返回了。\n\n代码事实（`src/backend/infrastructure/process_runner.py`）：\n\n- `_run_captured_process` 在 `input_text is None` 且 `inactivity_timeout` 不为 `None` 时走 `_communicate_with_activity_tracking(process, watchdog)`。\n- 该函数启动两个读线程后，只对两个线程各 `join(timeout=5)`，然后**直接** `return \"\".join(stdout_lines), \"\".join(stderr_lines)`。读线程要等到子进程关闭管道（EOF）才会结束，所以子进程运行超过约 5 秒时，这两次 `join` 都会超时，返回的是\"此刻已读到的内容\"。\n- 返回之后调用方才执行 `process.wait()`，此时子进程之后写出的 stdout 已经进不了返回值。\n\n典型现象：某些 agent CLI 的进度全走 stderr、**最终答复只在结束时写一次 stdout**。verifier 跑几分钟后，捕获到的 stdout 恒为空字符串，导致 `parse_verifier_verdict` 找不到 verdict marker，按 fail-safe 判红（日志里是 `No parseable verifier verdict marker found; treating as blocked (red).`，证据文件里是 `response chars: 0`）。\n\n### Expected Behavior\n\n对带 `inactivity_timeout` 的捕获型调用，返回值必须包含子进程**整个生命周期**写到 stdout/stderr 的全部内容：\n\n1. 先等子进程结束（保持现有的总超时、静默超时和取消逻辑不变），再收齐两个读线程的输出，最后返回。\n2. 收齐读线程时要有上界：子进程自己已退出但有孙进程继续持有管道时，不能让调用永久卡住；这种情况返回已读到的内容，并保持现有行为（不新增异常类型）。\n3. 超时、被取消时抛出的异常里携带的 `partial_stdout` / `partial_stderr` 语义不变。\n\n## Scope\n\n- 只修\"stdout/stderr 提前定格\"这一个缺陷，即 `_communicate_with_activity_tracking` 及其调用处。\n- **不在范围内**：codex 沙箱阻止浏览器启动、verifier 对浏览器类验证的降级策略，这是另一个独立问题。\n- 不改 `process.communicate(...)` 那条分支（带 `input_text` 或没有 `inactivity_timeout` 的情况）。\n\n## Acceptance Checklist\n\n- [ ] 新增回归测试（放在 `tests/test_process_runner.py`）：一个子进程持续往 stderr 写进度、运行时间明显超过读线程的 join 超时，最后才写一行 stdout 后退出；断言返回的 `stdout` 等于该行。该测试在修复前必须失败、修复后通过。\n- [ ] 新增测试：子进程退出后仍有孙进程持有管道时，调用在有限时间内返回（不卡死）。\n- [ ] 既有超时、静默超时与取消相关测试全部保持通过：`uv run pytest tests/test_process_runner.py -q`。\n- [ ] 若读线程 join 的等待时间需要常量，定义为模块级常量，不在函数里写魔法数字。\n- [ ] `just lint` 通过。\n",
  "body_sha256": "708f642df71f54b1db1899e5a7474cac684a3bd92eeb904296d612058448b2d1",
  "labels": [
    "type/bug",
    "priority/P1"
  ],
  "state": "open",
  "url": "https://github.com/ZataZhang/keda/issues/306",
  "base_sha": "e17f599a14a243dcdd1e7fefec04aa42674a0655",
  "head_sha": "e17f599a14a243dcdd1e7fefec04aa42674a0655",
  "base_ref": "zata/main"
}
```

## 证据绑定

- 真实运行发生时的代码：`c112e7cb`（含快照模块与接入点）。其后快照模块有一处加固（`repo_id` 为 `.` / `..` 时的路径清洗，提交 `8d8f7153`），接入点 `agent_runner_issue_handlers.py` 无变化；两者都不影响本次运行观察到的行为。
- 最终被验证的代码树：`dcd33bec3baf97186cece61ea14cd9cdaa74456c`（HEAD `8d8f715398`）。
