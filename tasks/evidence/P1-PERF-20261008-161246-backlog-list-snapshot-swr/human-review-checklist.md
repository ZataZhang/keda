# 人工审查清单 · Backlog 列表本地快照秒开与后台自动刷新（Issue #246）

> PRD：`tasks/pending/P1-PERF-20261008-161246-backlog-list-snapshot-swr.md`
> 状态：执行器已交付并自验通过（独立 verifier 结论见 PR），以下 3 项**只有你能回答**。
> 回复方式：逐项写明 **同意 / 不同意（附改法）**，或整页一句"三项均同意"。
> 交互式版本：`just prd review tasks/pending/P1-PERF-20261008-161246-backlog-list-snapshot-swr.md`（同目录 `human-review-checklist.html`，内容一致）。

**术语先说清**：
- **快照** = 列表数据在本地 SQLite 库里的一份落盘副本（页面读它秒开，不再现场等 GitHub）。
- **过期标记（`stale` 字段）** = 响应里新增的布尔字段，`true` 表示这份数据已超过 30 秒、后台正在重新扫描。
- **呈递物** = 给你肉眼核对的截图/录屏证据，不是机器测试日志。

---

## 第 1 项 · 是否同意新增 `backlog_prd_snapshots` 本地快照表

**你在决定什么**：Backlog 列表的数据要多存一份在 console 自己的 SQLite 库里（新表，只增不改）。
**如果判错**：若你其实不接受本地多一张表（例如意图是将来换存储引擎），这次交付的数据落盘方式就要整体返工。

PRD 原文（§9 Human-Confirmed · 决策一）：

> 决策一：同意为 Backlog 列表在本地库新增 `backlog_prd_snapshots` 快照表（表只增不改，快照数据可能是上次同步而非实时，页面如实标注）

白话展开：表结构只有 `repo_id + 视图变体 → 列表 JSON + 构建时间`；迁移只新增这一张表，既有表读写不受影响（有专项迁移测试）。数据可能滞后于 GitHub 实时状态，滞后时页面会显示"数据截至 HH:MM:SS / 后台更新中"，不装作实时。

证据：`tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/rv-2-snapshot-persistence.txt`（负控制：把落盘改成 no-op，4 个用例变红；恢复后全绿）。
可选复跑：`uv run python tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/scripts/rv_pytest_capture.py --item 2 --out /tmp/rv2.txt`

- [ ] 同意：接受新增快照表
- [ ] 不同意，改为：＿＿＿（请写明替代方案）

---

## 第 2 项 · 是否接受列表接口契约变化（快照 + stale 标记 + 后台更新）

**你在决定什么**：`GET /api/v1/agent-runner/backlog/prds` 的语义从"实时扫描结果"改为"立即返回本地快照，可能短暂陈旧，响应新增 `stale` / `scanned_at` 字段，过期时后台自动重扫"。
**如果判错**：若有调用方（脚本、外部工具）依赖"响应必然是实时值"，它们会读到最长一个同步周期内的旧数据。

PRD 原文（§9 Human-Confirmed · 决策二）：

> 决策二：接受列表接口契约变化——"立即返回快照（可能短暂陈旧）+ 显式过期标记 + 后台更新"，写接口与详情类接口语义不变

白话展开：只有这一个列表读接口变了；开始/全局开始等写接口、PRD 详情/证据/CI 等读接口的实时语义原样保留。陈旧窗口默认 ≤30 秒（页面 3 秒短轮询追平），周期刷新默认 5 分钟且可在 dashboard 关闭。

证据：`rv-1-first-paint-run.txt`（真实 console 重启后 15ms 返回快照、浏览器 165ms 出列表、表头"数据截至"）与 `rv-3-stale-contract.txt`（stale 判定、去重、fresh 恢复的 9 项契约测试；负控制拿掉后台触发后 7 项变红）。
可选复跑：`bash tasks/evidence/P1-PERF-20261008-161246-backlog-list-snapshot-swr/scripts/rv1_capture.sh`

- [ ] 同意：接受该契约变化
- [ ] 不同意，改为：＿＿＿（例如要求某调用方保持实时语义）

---

## 第 3 项 · §9.1 呈递面复核：两张截图与实际行为是否一致

**你在决定什么**：确认下面两个人眼可见的呈递物如实反映了你实际使用 console 时会看到的行为（秒开 + 新鲜度标注 / 过期提示出现又消失）。
**如果判错**：呈递物若与真实行为不符（例如截图来自组件预览而非真实入口），验收结论就不成立，需要重采证据。

PRD 原文（§9 Human-Confirmed）：

> §9.1 呈递面复核：两个呈递物（首屏秒开录屏/截图、stale→fresh 两帧截图）内容与实际行为一致

**呈递物 1 · 重启 console 后首屏秒开**（真实入口：`kc console` + chromium 打开 `/app/backlog/`，验证层级：real-entry）：

![rv-1 重启后首屏秒开](rv-1-first-paint.png)

预期看到：3 条真实 PRD 卡片 + 表头"数据截至 HH:MM:SS"。10 秒自检：本地 console 停留 Backlog 页刷新，列表立即出现且时间戳不变。

**呈递物 2 · 数据过期时"后台更新中"出现又消失**（真实入口：dev 栈 + Playwright 全真渲染，仅 mock 列表 API 时序——这正是被测契约本身；验证层级：e2e real-entry）：

![rv-7 stale→fresh 两帧](rv-7-stale-fresh.png)

预期看到：上帧"数据截至 + 后台更新中"，下帧新条目出现、"后台更新中"消失。

- [ ] 已逐项过目，与实际行为一致
- [ ] 有问题（请在补充里写明哪张图、差在哪）

---

*审查结果回填位置：PRD §9 Human-Confirmed；本清单保留为审查痕迹。*
