# 验证计划：认领 Issue 时留存原始任务输入快照

PRD：`tasks/archive/P2-FEAT-20261022-133921-issue-input-snapshot-at-claim.md`（§7.6 为唯一的 oracle 来源）。

| oracle | 审阅对象 | 层级 | 验证方式 | 证据 |
|---|---|---|---|---|
| rv-1 | 人 | R2 | 真实 `kc run --issue 306`（真实 GitHub Issue、真实 codex Agent），对照创建 Issue 时保存的正文，按字节比较 | `rv-1-snapshot-file.md` |
| rv-2 | verifier | R2 | `tests/test_agent_runner_issue_snapshot.py`：不重复、只追加、旧行字节不变、分叉点不等于 HEAD（真实 git） | `rv-2-rv-3-snapshot-pytest.txt` |
| rv-3 | verifier | R1 | 同一文件：写失败只告警、不抛错；含驱动 `_process_ready_issue` 的处理器层用例 | `rv-2-rv-3-snapshot-pytest.txt` |
| rv-4 | 人 | R1 | 带 `--with-gh` 对真实数据库导出：快照优先、`edited_since_claim`、旧样本不变；并有单元层的多快照与空白差异用例 | `rv-4-export-sample.md`、`rv-4-legacy-comparison.md`、`rv-exporter-pytest.txt` |

架构、规模、数据库版本、前端、文档与 mkdocs 的静态检查见 `rv-arch-and-docs-checks.txt`。

## 被验证的代码树

- HEAD `8d8f715398d5db06a493b3290e484963ce351ed8`，tree `dcd33bec3baf97186cece61ea14cd9cdaa74456c`。
- 按交付记录的约定，验证树不含 PRD 文件与本证据目录（它们在验证之后才写入/移动）。

## 不做的验证

- 未构建"隔离环境 + 假 gh + 探针 Agent"那一组（PRD 原计划），由真实运行取代；理由与影响见 PRD 最终对账。
