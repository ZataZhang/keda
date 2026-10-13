## 人审导航 / Human Review Navigation

- PRD：`tasks/archive/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md`（交付随 PR 归档）
- PR / CI：尚未创建；生产实现已完成并采集 rv-1/rv-2/rv-3 证据（见 `evidence.json`）。
- 人审入口：启动 `uv run kc console`，打开命令输出的 loopback origin 下 `/app/evaluations/`。
- 验证层级：以下 production capture 均来自真实 Console 页面 + 真实 SQLite + 隔离 fixture repo；
  唯一替身边界是 judge provider（本地 mock OpenAI-compatible 服务，真实 `create_chat_model`
  与真实 HTTP 调用计数），live provider 按 PRD 仅走用户 opt-in，未在本证据中执行。

### 1. 固定样本下比较执行预设与多个评审模型（rv-1）

![真实页面评测结果](rv-1-evaluation-results.png)

> 验证层级：**production capture**（headless Chromium 驱动真实 `/app/evaluations/` 页面完成
> 创建 → 启动 → 预览授权 → 已完成全流程）。

同 suite/base、2 执行预设 × 2 评审预设共 4 候选、8 条逐评审评分行；checks（alpha passed /
beta failed）与评分分列；每维中位数可由 fresh SQLite 明细独立重算一致（
`rv-1-sqlite-fresh-read.txt`、`rv-1-run-detail.json`、`rv-1-tree-stamps.txt`）。窄屏
390×844 无横向溢出（`rv-1-narrow-390x844.png`，负控注入 900px 溢出块后探针变红）。

### 2. 评分模型外发内容预览与逐次授权（rv-2）

![真实页面授权预览弹窗](rv-2-payload-consent.png)

![页面拒绝后的未授权状态](rv-2-after-page-deny.png)

> 验证层级：**production capture**（同一真实浏览器流程分别走拒绝与批准两条路径）。

预览 payload 与暂存文件逐 hash 对齐、含真实 diff 与目标模型名；页面拒绝后 mock provider
请求数为 0、8 条 `unauthorized` 行、聚合为空；篡改暂存文件后旧 hash 批准返回 409 且零外发；
还原原始字节后按冻结 hash 批准成功并恰好产生 8 次 mock 调用（模型分布 4+4）。负控全文见
`rv-2-consent-negative-control.txt`。

### 3. 退化状态可解释性（rv-3，报告见 `rv-3-degraded-states.json`）

非法 suite recipe / 缺 provider / 候选超时 / 拒答 / 坏 schema / 500 / 取消 / kill -9 自愈 /
级联删除均在真实 route + SQLite 上验证：失败态不折算 0 分（SQL 注入伪 0 分后聚合 oracle
变红、还原后恢复），正式 Issue/PR/生命周期账本逐表不变，重启后 run 标 `interrupted` 且
候选 Agent 调用数不变（无自动重跑）。

### 执行者已替你核对过什么

- 所有 rv 证据在**无任何真实 API key** 的机器上采集；judge provider 边界以本地 mock 服务
  做真实 HTTP 请求计数，已如实披露，未把 mock 称为 live。
- 每次 rv 都先跑负控（badref 400、中位数敏感、拒绝零外发、篡改 409、伪 0 分检出、窄屏溢出
  注入）确认 oracle 会变红，再跑绿。
- 临时 fixture suite（`rv284-*.yaml`）与隔离 worktree/暂存 payload 在运行结束后均已清理，
  不进入代码 diff。
