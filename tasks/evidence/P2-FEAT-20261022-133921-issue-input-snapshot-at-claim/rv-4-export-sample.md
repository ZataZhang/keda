# rv-4：导出器优先使用认领时快照，并标出认领后被编辑

> 验证层级：**live integration**——对真实 `console.db`、真实 git、真实 GitHub（`--with-gh`）运行 `python -m scripts.dataset_export`。

## 要看什么

对一个"有快照、且 GitHub 上正文之后被改过"的 Issue，导出的样本取最早快照作为任务输入，并标出认领后被编辑；没有快照的旧 Issue 导出结果不变。

## 场景

Issue #306 认领后，我临时在 GitHub 上给正文末尾追加了一段说明（"验证用临时编辑，稍后还原"），然后带 `--with-gh` 导出；导出完成后用同一份原文把正文**按字节还原**（见 rv-1）。因此**现在重跑同一条导出命令，`edited_since_claim` 会是 `false`**；要复现 `true`，需要重新编辑一次 Issue 正文。

## 并列对照：快照里的正文 vs. 导出时 GitHub 上的正文

```diff
--- 快照里的正文（认领时）
+++ GitHub 现状正文（导出时）
@@ -35 +35,5 @@
 - [ ] `just lint` 通过。
+
+
+
+> (验证用临时编辑，稍后还原)
```

## 10 秒自验

| 检查项 | 期望 | 实际 |
|---|---|---|
| 输入来源 | `input.provenance` 为 `snapshot_at_claim` | **snapshot_at_claim** |
| 正文是认领时的原文 | 不含临时加的说明 | **True**（见上方对照：那一段只出现在"GitHub 现状正文"一侧） |
| 认领后被编辑 | `input.edited_since_claim` 为 `true` | **True** |
| 代码起点来自快照 | `code.base_sha_source` 为 `snapshot`，且等于快照里的 `base_sha` | **snapshot**；等于快照：**True**（`e17f599a14a2`） |
| 没有快照的旧 Issue 不变 | 其余样本的任务输入、提交、已解决、规模字段与改动前一致 | 共同样本 **74** 个：**73 个**一致，**1 个**不同（原因：其 PR 在两次导出之间被合并，与快照功能无关）；逐样本明细见 `rv-4-legacy-comparison.md` |

## 样本摘录（#306，已省略较长的 `body`，其内容见上方对照）

```json
{
  "input": {
    "provenance": "snapshot_at_claim",
    "title": "[Bug] 带静默超时的捕获型子进程提前返回，丢失最终 stdout（verifier verdict 丢失）",
    "edited_since_claim": true
  },
  "code": {
    "source": "pull_request",
    "base_sha": "e17f599a14a243dcdd1e7fefec04aa42674a0655",
    "base_sha_source": "snapshot",
    "final_sha": "7f25d483721e2d71d9a79a805b2ce8710dc0b23b"
  },
  "labels": {
    "resolved": false,
    "resolved_basis": "pr_merged",
    "size_band": "small",
    "eval_ready": false
  }
}
```

## 局限（如实说明）

- 与 PRD §7.6 原计划不同：原计划用 fixture 数据库与假 gh，实际用的是真实 `console.db` 和真实 `gh`（保真度更高）；PRD 的 oracle 已据此改写。
- "改动前的导出"是一份保存下来的本地文件，不在仓库里；比对结果与两份文件的哈希写在 `rv-4-legacy-comparison.md`，但文件本身无法随仓库复现。
- 旧样本因此多了两个新键（`input.edited_since_claim: null`、`code.base_sha_source: "inferred"`），其余值不变；这是新增字段，不是改变原有字段。
- 本次导出之后，导出器的"是否被编辑"比较改为忽略换行风格与末尾空白（提交 `8d8f7153`，因为我自己还原正文时就因多一个末尾换行造成过假阳性）；上面的 `true` 来自一个内容上的真实编辑，不受影响。
