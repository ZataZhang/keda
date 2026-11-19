# Human Review Checklist

独立 verifier 已对实现 SHA `525a3549ead8a4ada725b7916c694ba215fb8fa8` 完成 rv-1 并返回 `PASS`；归档 PRD 状态为 `🧍 待人工验收`。PR #404 是当前审阅入口，但其标题仍缺少 `cleanup(ci):` 前缀，描述仍固定到旧 SHA 并列出旧的 99 文件统计。rv-1 与 runtime-schema 原始输出仍待发布到 `kc-evidence/issue-366` 并写入 PR evidence comment，因此本清单暂不请求人工接受。GitHub comment 与 PR 元数据写入被连接器审批门拒绝（需要审批，但当前策略为 `never`）；证据分支发布又受本任务 no-push 约束限制。复核时请确认是否接受 CI 失败修复的范围和记录。若判断错误，后续 nightly 可能重复处理已解决工作，或丢失尚未审阅的文档范围。

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
- 原始 rv-1 与 runtime-schema 输出仍在本地忽略文件中；`kc-evidence/issue-366` 归档和 PR evidence comment 均待 runner 发布。

**请回复：** “同意”，或指出具体差异及需要补充的验证 / 后续 Issue。

可选复核命令（放在最后，供需要时深入检查）：

```bash
git diff -- tests/test_kedacode_operator_skill.py
open tasks/evidence/P1-FEAT-20261113-121113-nightly-cleanup-keda/P1-FEAT-20261113-121113-nightly-cleanup-keda.evidence-report.md
```
