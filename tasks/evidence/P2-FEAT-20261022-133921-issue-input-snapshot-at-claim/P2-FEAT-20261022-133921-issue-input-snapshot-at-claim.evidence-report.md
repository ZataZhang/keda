## 人审导航 / Human Review Navigation

只需要看下面两项，约几分钟。两项都是文本证据，没有截图，因此没有内嵌图片。

| 要看的结果 | 在哪里（仓库相对路径） | 打开方式 | 10 秒自验 |
|---|---|---|---|
| rv-1：认领后快照里留下了任务原文，起点正确（真实 `kc run` + Issue #306） | `tasks/evidence/P2-FEAT-20261022-133921-issue-input-snapshot-at-claim/rv-1-snapshot-file.md` | `just prd review tasks/archive/P2-FEAT-20261022-133921-issue-input-snapshot-at-claim.md` | "正文逐字一致"与"SHA-256"两行为 True；快照文件 1 行；仓库内没有快照文件 |
| rv-4：导出样本使用认领时快照并标出被编辑 | `tasks/evidence/P2-FEAT-20261022-133921-issue-input-snapshot-at-claim/rv-4-export-sample.md`（并列 diff）、`rv-4-legacy-comparison.md`（旧样本比对） | 同上 | `provenance` 为 `snapshot_at_claim`；`edited_since_claim` 为 `true`；并列 diff 里临时加的说明只出现在 GitHub 一侧 |

相关链接：真实运行所用的 Issue <https://github.com/ZataZhang/keda/issues/306>，及其产出的 PR <https://github.com/ZataZhang/keda/pull/307>（已合并）。本 PRD 的快照功能本身没有对应 PR。

**已由执行者交叉核对过**：快照正文与创建 Issue 时保存的正文逐字节一致，当前 GitHub 正文也已按字节还原并取回比较；16 个快照用例与 32 个导出器用例通过；整个多阶段运行之后快照仍只有 1 行。**另已通过一次独立 verifier 复核**（见 `P2-FEAT-20261022-133921-issue-input-snapshot-at-claim.verifier-report.md`），其发现的问题已修复并写入 PRD 最终对账。

---

## 结果汇总（被验证的代码树 `dcd33bec3baf97186cece61ea14cd9cdaa74456c`，HEAD `8d8f715398d5db06a493b3290e484963ce351ed8`）

| oracle | 结论 | 证据 |
|---|---|---|
| rv-1 | 通过 | 快照 1 行；`body`、`body_sha256`、`schema_version`、字段清单、标签、起点均符合预期；仓库内无快照文件 |
| rv-2 | 通过 | 快照测试 16 个全部 PASSED，含"相同正文不重复""正文改动追加且旧行字节不变""起点是分叉点而非 HEAD""路径不会逃出快照区" |
| rv-3 | 通过 | `test_ready_handler_continues_after_snapshot_failure`（驱动处理器）与 `test_snapshot_failure_only_warns_and_never_raises` 均 PASSED |
| rv-4 | 通过 | 带快照样本 `snapshot_at_claim` + `edited_since_claim=true` + `base_sha_source=snapshot`；74 个共同旧样本中 73 个字段一致，1 个因其 PR 被合并而不同（与本功能无关） |

## 重跑与静态检查（在上述代码树上）

- `uv run pytest tests/test_agent_runner_issue_snapshot.py tests/test_dataset_export.py`：48 passed（`rv-exporter-pytest.txt`）。
- 架构/规模/数据库版本/前端/文档检查：全部符合预期（`rv-arch-and-docs-checks.txt`）。
- `uv run mkdocs build -q`：退出码 0。
- `just test`（含 `just lint --full`）：通过（本树上 111 passed）。

## 偏差与局限（如实）

1. rv-1、rv-4 由真实运行取代了计划中的隔离场景/fixture（保真度更高，但不是原计划那一组）。
2. 运行前后的 `git status --porcelain` 当时未逐次留档。
3. rv-1 的真实运行发生在代码 `c112e7cb`；此后快照模块仅有 `repo_id` 路径清洗的加固，接入点无变化，但没有在最终树上重新跑一次真实 `kc run`。
4. 认领那一刻的 GitHub 正文无法事后重放；依据是创建与认领之间没有编辑、且快照正文与保存的原文字节一致。
5. rv-4 的"改动前导出"是一份本地临时文件，不在仓库里；比对结果写在 `rv-4-legacy-comparison.md`，但原文件不可随仓库复现。
6. 每一条 `negative_control` 都是"not needed"（实现前入口不存在、或比较的是具体值）；另外独立 verifier 做了 8 个变异检查，其中唯一存活的一个（导出器取最后一条快照）已用新增用例杀掉。
