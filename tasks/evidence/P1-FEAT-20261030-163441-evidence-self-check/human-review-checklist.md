# 人工验收清单：Agent 证据自检入口（kc evidence check）

PRD：`tasks/pending/P1-FEAT-20261030-163441-evidence-self-check.md`（runner 归档后位于 `tasks/archive/`）
待人工确认项：2 项（PRD §2 的决策一、决策二）。执行侧验收项已全部勾选并附证据，独立 verifier 由 runner 运行。

**回答方式**：逐项回复「同意」或「选择另一方案」；若不同意，请指出要改哪一句与原因。

---

## 1. 决策一：本地自检允许比门禁更短的超时

**你在决定什么**：是否允许 `kc evidence check --timeout <秒>` 把单条命令的超时调得比交付门禁更短，只用于本地快速迭代；调小后的超时不得大于门禁配置上限（默认 300 秒）。

**判断错了会怎样**：若不允许而实现却提供了该参数，Agent 可能用极短超时得到假失败、误以为证据有错；若允许但不设上限，自检就会比门禁更宽松，放过门禁会拦截的证据。

**PRD 原文**：

> **请确认：** 是否允许 `kc evidence check --timeout <秒>` 只用于本地快速迭代，且不得大于配置上限？
>
> **验收：** 用 `--timeout 1` 跑一条需要数秒的题目，输出标注本地超时并提示门禁上限；不传该参数时行为与门禁完全一致。

**当前实现与证据**：

- `--timeout 1` 时输出 `delivery gate limit is 300s`，慢题标为 `TIMEOUT`，其余题仍给出结论；`--timeout 301` 以用法错误退出（退出码 2）。
- 证据文件：`rv-3-timeout-isolation.txt`（含负控：去掉 `--timeout` 后同一 fixture 通过）。

**请回复**：同意 / 选择另一种方案（说明）。

---

## 2. 决策二：门禁失败改为一次报全部

**你在决定什么**：是否接受交付门禁的失败信息从「遇到第一条失败就停」改为「收集全部未通过项后聚合成一条错误」。

**判断错了会怎样**：若保持第一条失败即停，Agent 每轮只能发现一条证据错误，反复消耗整轮执行时间；若聚合但片段不限长，Issue 评论会变得难以阅读。

**PRD 原文**：

> **请确认：** 是否接受门禁失败信息从"第一条失败"改为"全部失败项的聚合摘要"？
>
> **验收：** 构造两条不相干失败（一条断言不匹配、一条超时），门禁一次报出两条，各自带命令与失败类别。

**当前实现与证据**：

- `ensure_validation_commands_pass` 复用自检编排，一次聚合全部未通过项；单条输出沿用现有截断口径。
- 证据文件：`rv-1-self-check-vs-gate.txt` 展示门禁聚合消息；单元测试 `test_ensure_validation_commands_pass_reports_every_failing_item` 与 `test_ensure_validation_commands_pass_aggregates_assertion_failures` 覆盖两条不相干失败同时报出。

**请回复**：同意 / 选择保持现状（说明）。

---

## 复核入口

```bash
# 真实证据包自检（应与归档时一致：5/5 passed，退出码 0）
kc evidence check --evidence tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check --json

# 完整验证计划与逐项证据说明
open tasks/evidence/P1-FEAT-20261030-163441-evidence-self-check/P1-FEAT-20261030-163441-evidence-self-check.evidence-report.md
```
