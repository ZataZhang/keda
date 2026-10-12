## 人审导航 / Human Review Navigation

- PRD：`tasks/pending/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md`
- PR / CI：尚未创建；本次交付是待办 PRD 与交互原型，不包含生产实现。
- 人审入口：启动 `uv run mkdocs serve -a 127.0.0.1:8000`，然后打开 [模型评测原型](http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html#evaluations)。
- 当前验证边界：以下两张图均为 component preview / design intent。候选运行、分数、provider 和授权交互均为静态 fixture，不会产生模型调用或费用。

### 1. 固定样本下比较执行预设与多个评审模型

![多执行模型、多评审者的结果对照目标态](../../../docs/prototypes/assets/runtime-evaluation-preview.png)

> 验证层级：**component preview / design intent**；候选结果、checks、各 judge 分项及统计均为静态 fixture。
> 本地图已纳入仓库版本控制，可从仓库根目录运行：`open docs/prototypes/assets/runtime-evaluation-preview.png`

逐项期望值：候选执行预设使用相同 suite/base；确定性检查与模型评分分开显示；每位评审者的维度评分可单独查看；聚合展示各维度中位数、有效数与范围，不生成默认总分。实际 suite/worktree 隔离、SQLite fresh-read 与多个评分模型请求仍待实现后验证。

### 2. 评分模型外发内容预览与逐次授权

![评审 payload 外发授权目标态](../../../docs/prototypes/assets/runtime-consent-preview.png)

> 验证层级：**component preview / design intent**；画面中的批准/拒绝是本地模拟状态，不连接任何 provider。
> 本地图已纳入仓库版本控制，可从仓库根目录运行：`open docs/prototypes/assets/runtime-consent-preview.png`

逐项期望值：用户可看到目标 provider/model、任务说明、执行摘要、Diff 和 checks，再选择拒绝或授权；拒绝时应无评分请求。当前原型不发请求、不扣费，准确 payload/hash 对照、拒绝负控、费用和临时数据清理均须在生产实现中验证。

### 执行者已替你核对过什么

- 原型展示多个候选和多个 judge 的独立结果，并将检查状态、运行指标和评审分数分开呈现；未构造“综合评分”。
- Prototype Hub 已登记结果与授权状态并提供来源 sidecar；Hub 至原型再返回 Hub 的真实浏览器路径已验证，窄屏视口无横向溢出，采集时 `pageErrors=0`。
- 候选 Agent 启动与 judge 外发授权在 PRD 中明确分开：启动候选前披露已选 provider/model 与可得费用；评分请求则必须逐次预览和授权。
- 没有连接真实模型 provider、执行候选 Agent 或持久化评测 run；当前待办 PRD 未实现功能，因此没有生产截图、PR 或 CI 结论。
