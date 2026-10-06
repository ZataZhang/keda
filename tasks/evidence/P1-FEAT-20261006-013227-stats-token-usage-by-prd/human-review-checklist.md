# 人工验收清单 · Stats 页 Token 用量补「按 PRD」维度汇总（Issue #209）

- PRD：`tasks/pending/P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`（交付时由 runner 归档到 `tasks/archive/`），横幅为 🧍 待人工验收。
- Issue：ZataZhang/keda#209。代码 PR 由 runner 创建；PR 正文带"合并即验收"声明，**合并就等于下面 2 项都同意**，不必再到对话里逐条回。
- 状态：**执行侧已交付（rv-1..rv-5 全绿、各自负控全红），等你对 2 项 `Human-Confirmed` 过目表态。** 独立 verifier 复核与归档仍是 runner-owned `[~]`，不由本清单代答。
- 证据报告：`open "tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/P1-FEAT-20261006-013227-stats-token-usage-by-prd.evidence-report.md"`
- 交互版：`just prd review tasks/pending/P1-FEAT-20261006-013227-stats-token-usage-by-prd.md`（或 `open "tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/human-review-checklist.html"`）

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。两项都同意时一句"两项都同意"就够了。

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | §1 行为样例表五行逐行符合：三张表同屏、页面与 CLI 数字一致、多次执行合并一行并显示执行次数、无消耗 PRD 不出现且空态明确、另两张表数字不变 | §1 行为样例 → §9.2 Human-Confirmed 第 1 项 | 这五行就是本功能的验收口径；若哪一行与你的预期不符（比如希望零消耗 PRD 也出现、或不想要"执行次数"列），现在改是加一列/改一行渲染，合并后再改就是二次破坏响应契约 |
| 2 | `stats/prd-lifecycle` 响应新增 `token_usage_by_prd` 字段（唯一对外变更面，只增不改）可接受，无需向已知消费者发公告 | §3 → §9.2 Human-Confirmed 第 2 项 | 若有你不知道的外部消费者按精确响应形状校验（如严格 schema），升级后会立刻报错；届时需要公告或另开兼容 PRD（本 PRD 明确不预留） |

**术语先解释一句**

- *同源同口径*：页面与 `iar tokens` 读同一份 SQLite 账本、走同一个聚合函数、用同一个窗口钳制规则，数字不是"对得差不多"而是逐格相等。
- *只增不改*：既有字段的名称、类型、数值规则一字不动，只在响应里多出一个新数组字段。
- *负控*：把被验证的东西还原成"错误状态"再跑同一批断言，确认它会红；绿才有意义。
- *`[~]`*：清单里"等 runner 门禁"的标记，不是"已完成"。

---

## 第 1 项 · §1 行为样例五行逐行确认

**你在确认什么**：交付出的页面行为与 PRD §1 的五行样例逐行一致。请对照下面两张真实截图（同一时刻、同一账本、同一代码树采集）逐行看：

1. **三张表同屏**：「Token 用量」区依次为 按流程、按 agent、按 PRD（见 `rv-4-token-usage-section.png`）。
2. **页面与 CLI 一致**：`#194` 两侧总量同为 `7133.1k`（见 `rv-2-page-by-prd.png` 与 `rv-2-cli-tokens.png` 并排；全部 9 行逐格相等，机器断言见 `rv-2-cli-vs-page.txt`）。
3. **多次执行合并一行**：`#193` 在 CLI 出现两行是因为它是**两个不同 PRD 文件**（分组键含 PRD 路径）；同一 PRD 的多次 run 合并为一行、`执行次数` 列显示累计条数（后端断言见 `tests/test_agent_token_stats.py::test_same_prd_multiple_runs_merge`）。
4. **无消耗 PRD 不出现 + 空态明确**：表里不渲染全零行；空窗口时显示"所选范围内暂无 token 用量数据。"（e2e 用例 2、3）。
5. **另两张表数字不变**：按流程/按 agent 与改动前逐字节一致（`rv-5-no-regression.txt`）。

**证据**：`tasks/evidence/P1-FEAT-20261006-013227-stats-token-usage-by-prd/` 下 rv-2 / rv-4 截图与 txt；负控（页面值 ×2、摘除第三张表）均实测为红。

**回答方式**：对每一行回复「符合」，或指出哪一行与预期不符。全部符合时一句"五行都符合"。

## 第 2 项 · 响应新增字段的可接受性

**你在确认什么**：`GET /api/v1/agent-runner/console/stats/prd-lifecycle` 的响应多出一个 `token_usage_by_prd` 数组字段，这是本次唯一的对外变更面；既有字段一字不动（rv-1 / rv-5 已证）。你接受它作为唯一的破坏性面候选，且不需要额外公告。

**PRD 原话（§3）**：只增字段、既有消费者不受影响；文档已在 `docs/guides/agent-runner.md` 公告字段名与语义。

**回答方式**：「可接受」，或列出你知道的需要公告的外部消费者。
