# 人工验收清单 · 生命周期时间线接入调用明细与展示去噪

- PRD：`tasks/archive/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md`（执行侧已交付、横幅 🧍 待人工验收；归档已由 runner 在交付时执行，PR 由 runner 在执行器门禁后完成）。
- 状态：**执行侧已交付；独立 verifier 结论由 runner 的 verifier 轮次写入同目录 `….verifier-report.md`；等你对 2 项表态。** 这 2 项就是 PRD §9.2 `Human-Confirmed` 的两个空框。
- 交互版（推荐，含截图与选项）：`just prd review tasks/archive/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.md`
- 证据报告：`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline.evidence-report.md"`

**怎么回复**：每项只回 `同意`，或 `有差异：<你的说明>`。全部同意时，一句「两项都同意」就够了。

| # | 你在确认什么 | PRD 位置 | 判错的代价 |
|---|---|---|---|
| 1 | 调用明细按「执行会话 → Agent 尝试 → 调用」分层接入，三类缺口显式披露、不推测回填 | §2 决策 → §9.2 Human-Confirmed 第 1 项 | 视图最坏的表现是"看起来完整"：调用被错误归组或被静默省略，值守者复盘"卡在哪、要不要接管"时被误导 |
| 2 | §9.1 的两个呈递物（真实页面截图、可点击原型）你已逐项过目并认可 | §9.1 → §9.2 Human-Confirmed 第 2 项 | 这一项没看就确认，第 1 项的"验收"就只剩机器背书 |

**术语先解释一句**

- *执行会话（run）*：runner 处理一个 Issue 的一次完整执行，一个 PRD 可以有多轮。
- *Agent 尝试（attempt）*：一次会话里的一次尝试，失败后会重试并递增尝试编号。
- *调用（invocation）*：一次真实进程调用（编写代码、运行验证等），起止成对写入本机调用账本。
- *调用账本*：本机 SQLite（`~/.kedacode/console.db` 的 `agent_invocation_events` 表），与 `kc logs` 的 `[iar-invocation-*]` 标记同源同口径。
- *缺口披露*：`available=false`（账本读不到）、无会话归属（旧事件没有标识）、`truncated=true`（读取触达上限）三种如实说明。
- *verifier*：runner 的独立复核轮次，只读，结论是 `PASS` 或 `REJECT`。
- *oracle*：PRD 里事先写好、能逐条判真假的验收判据，即 rv-1…rv-4。

---

## 决策一 · 调用明细按「会话 → 尝试 → 调用」分层接入，缺口显式披露、不推测回填

**你在拍板的事**：把已经落库的调用事实接进「执行过程」时间线，按「执行会话 → Agent 尝试 → 调用」分层；旧数据没有会话标识时回落平铺并说明「无归属」，账本不可用与读取截断也如实披露，绝不按时间窗口猜测分组或生成假调用。**如果判错**：最坏的形态是页面"看起来完整"——调用被错误归组或被静默省略，值守者在复盘时误判 agent 行为、耽误接管。

**PRD 原话（§2 决策，逐字引用）**：

> 建议把已落库的调用事实接进既有「执行过程」视图：时间线按"执行会话 → Agent 尝试 → 调用"分层，每次尝试可展开看到真实调用序列；同时把三类缺口——旧数据无会话归属、调用账本读取失败、读取达到上限——在页面显式披露，而不是猜测或静默省略。……**请确认：** 是否同意按该分层形态接入调用明细，并接受"缺口必须显式披露、不得推测回填"的降级口径？

**证据（rv-1：真实 Console + 真实 SQLite，独立 fresh read 与页面逐条比对 PASS）**：

![rv-1 桌面：执行会话分组 + Agent 尝试展开调用明细（真实页面、真实账本）](rv-1-lifecycle-invocation.png)

![rv-1 1100px 窄窗口：同一页面、同一数据](rv-1-lifecycle-invocation-narrow-window.png)

local-only 说明：证据图不进版本库（`tasks/evidence/**` 被 gitignore，只提交 `.md`），在 GitHub 上不渲染。打开命令：
`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/rv-1-lifecycle-invocation.png"`
页面调用行（3 行）与账本独立 fresh read 的 3 行逐条一致：`rv-1-page-vs-ledger.txt`；三档页面观测见 `rv-1-page-rows.json` / `rv-1-page-rows-narrow.json` / `rv-1-page-rows-narrow-window.json`。

**怎么答**：

- `同意` —— 分层形态与"缺口显式披露、不推测回填"的降级口径都按此交付。
- `有差异：<说明>` —— 例如希望缺口披露更醒目/更轻，或希望旧数据也按某种方式归组（PRD 明确不做）。

---

## 决策二 · §9.1 两个呈递物已逐项过目并认可

**你在拍板的事**：§9.1 的两项呈递物（真实页面截图、目标形态可点击原型）你已经看见并认可。**如果判错**：这一项没看就确认，决策一的"验收"就只剩机器背书。

**PRD 原话（§9.2 Human-Confirmed 第 2 项）**：

> 人工完成 §9.1 两个呈递物的一次性审阅，并记录可接受或差异。

**证据**：两种视口的实现截图见决策一；原型的桌面与窄屏往返（五套场景全走通）：

![原型桌面往返](prototype-roundtrip-desktop.png)

![原型窄屏往返（420px）](prototype-roundtrip-narrow.png)

打开命令：`open "tasks/evidence/P1-FEAT-20261010-092630-lifecycle-invocation-detail-timeline/prototype-roundtrip-desktop.png"`
本地原型：`uv run mkdocs serve` 后打开 `http://127.0.0.1:8000/prototypes/prd-lifecycle-observability.html`（Hub 入口 `/prototypes/hub.html`）。

**怎么答**：

- `同意` —— 两个呈递物都已看过、可接受。
- `有差异：<说明>` —— 写清哪一项、差在哪、希望怎么改。

---

### 附注 · 需要一并知道的三处基线事实（非本 PRD 引入，已在 PRD §9.1 披露）

1. 基线 `44a33d6b` 上 `just test all` 有 5 个失败（`kc lifecycle` 子命令上线时漏同步守卫与随包 skill 计数），本次一并修复，全量测试回到 `3830 passed, 1 skipped`。
2. 基线前端 eslint 已有 5 个 `set-state-in-effect` 错误（位于本 PRD 未触及的文件；CI 的 frontend job 只跑 typecheck + build），因此前端 lint 的验收口径写「无新增错误」。若你要求前端 eslint 全绿，应另开 PRD 处理基线。
3. 420px 视口下真实 Console 的多栏布局横向溢出（既有布局、非本 PRD 改动），时间线被挤出视口；窄屏可用性由 1100px 窄窗口截图与原型 420px 往返共同证明。
