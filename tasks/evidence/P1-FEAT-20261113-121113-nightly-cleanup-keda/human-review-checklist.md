# Human Review Checklist

独立 verifier 已对实现 SHA `525a3549ead8a4ada725b7916c694ba215fb8fa8` 完成 rv-1 并返回 `PASS`；归档 PRD 状态为 `🧍 待人工验收`。只读核对确认 PR #404 仍为 draft，远端 head（核对时）是 `8043db827fd985be40df76ab1a604df9d97fb614`，当前比较为 7 个文件、495 additions / 105 deletions；现有标题仍缺少 `cleanup(ci):` 前缀，描述引用旧发布 SHA `525a3549ead8a4ada725b7916c694ba215fb8` 并列出旧 99 文件清单，PR 评论时间线为空。建议标题为 `cleanup(ci): align skill flag allowlist with CLI`。两份完整原始输出仍在本 worktree 的忽略文件中，哈希见证据报告；必须先发布到 `kc-evidence/issue-366` 并写入 PR evidence comment，之后再请求人工接受。本轮约束只允许修改 worktree 文件并禁止 push，因此以上远端更新仍待 runner 执行。

## 1. CI 修复范围与文档检查进度

**PRD 锚点：** §9.2 Human-Confirmed 第 1 项。

**PRD 原文：**

> Review the §9.1 code/evidence presentation and accept that this round fixes only the CI test allowlist while preserving the docs backlog as unconfirmed.

**白话说明：** 本轮只在测试白名单中登记 CLI 已支持的 `--local` 选项；没有改 CLI 行为。最新 CI 的失败来自测试白名单漏登记该选项。Docs sync 因 CI 优先而未执行；上一轮关闭 Issue #325 的评论没有 `checked_through_sha`，所以文档范围仍是“未确认”，不能算成首次运行或已检查无差异。

**已交叉核对的证据：**

- [CI 运行 44548773786](https://github.com/ZataZhang/keda/actions/runs/44548773786)：固定 head `4c58ad0df1b1437ea2336303a64d486ada00ea89` 上的 `test_packaged_skill_command_examples_match_cli_help` 报告 `--local` 未登记。
- [代码差异](../../../tests/test_kedacode_operator_skill.py)：只在 `("agent", "setup")` 白名单增加 `--local`。
- [本轮证据报告](P1-FEAT-20261113-121113-nightly-cleanup-keda.evidence-report.md)：记录 focused test、`just test`、lint、文档构建结果及未执行的 scope。
- [归档 PRD](../../archive/P1-FEAT-20261113-121113-nightly-cleanup-keda.md)：状态为 `🧍 待人工验收`，rv-1 独立验证为 `PASS`。
- 原始 rv-1 与 runtime-schema 输出仍在本地忽略文件中；`kc-evidence/issue-366` 归档和 PR evidence comment 均待 runner 发布。报告中的远端 PR 元数据为 2026-11-20 本轮本地修复前的只读快照，runner 提交后应把 PR 描述的 head 更新为新提交。

**请回复：** “同意”，或指出具体差异及需要补充的验证 / 后续 Issue。

可选复核命令（放在最后，供需要时深入检查）：

```bash
git diff -- tests/test_kedacode_operator_skill.py
open tasks/evidence/P1-FEAT-20261113-121113-nightly-cleanup-keda/P1-FEAT-20261113-121113-nightly-cleanup-keda.evidence-report.md
```
