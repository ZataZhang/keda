## 人审导航 / Human Review Navigation

- PRD：`tasks/archive/P1-FEAT-20261012-103909-multi-judge-agent-evaluation.md`（交付随 PR 归档；评审改走执行器的变更见其 Change Log「评审改走现有 Agent 执行器」）
- PR / CI：PR #286（draft）；rv-1/rv-2/rv-3 证据已按新方案重采集（见 `evidence.json`）。
- 人审入口：启动 `uv run kc console`，打开命令输出的 loopback origin 下 `/app/evaluations/`。
- 验证层级：以下 production capture 均来自真实 Console 页面 + 真实 SQLite + 隔离 fixture repo；
  唯一替身边界是评审 agent CLI 本身：评审预设即执行预设，评审调用经真实
  `run_agent_with_prompt(profile="judge")` 起子进程，子进程是 fixture 评审 CLI
  （`scripts/fixture_judge.py`，逐次记录调用数、进程 cwd 目录内容与收到 prompt 的 sha256）。
  真实 claude / codebuddy / qoder 评审按 PRD 仅走用户 opt-in，未在本证据中执行。

### 1. 固定样本下比较执行预设与多个评审预设（rv-1）

![真实页面评测结果](rv-1-evaluation-results.png)

> 验证层级：**production capture**（headless Chromium 驱动真实 `/app/evaluations/` 页面完成
> 创建 → 启动 → 预览授权 → 已完成全流程）。

同 suite/base、2 执行预设 × 2 评审预设共 4 候选、8 条逐评审评分行；checks（alpha passed /
beta failed）与评分分列；每维中位数可由 fresh SQLite 明细独立重算一致（
`rv-1-sqlite-fresh-read.txt`、`rv-1-run-detail.json`、`rv-1-tree-stamps.txt`）。8 次评审进程
调用的 cwd 均为空临时目录（`JUDGE_CWD_EMPTY_TEMP_DIR: True`）。窄屏 390×844 无横向溢出
（`rv-1-narrow-390x844.png`，负控注入 900px 溢出块后探针变红）。

### 2. 评审外发内容预览与逐次授权（rv-2）

![真实页面授权预览弹窗](rv-2-payload-consent.png)

![页面拒绝后的未授权状态](rv-2-after-page-deny.png)

> 验证层级：**production capture**（同一真实浏览器流程分别走拒绝与批准两条路径）。

预览 payload 与暂存文件逐 hash 对齐、含真实 diff 与目标评审预设；页面拒绝后评审进程调用数
为 0、8 条 `unauthorized` 行、聚合为空；篡改暂存文件后旧 hash 批准返回 409 且零外发；
还原原始字节后按冻结 hash 批准成功并恰好产生 8 次评审进程调用（行为分布 4+4），且评审进程
实际收到的 prompt 的 sha256 集合与授权 manifest 的 `payload_sha256` 集合逐一相等
（`JUDGE_PROMPT_SHA256_EQUALS_APPROVED_PAYLOADS: True`，即所批即所发）。负控全文见
`rv-2-consent-negative-control.txt`。

### 3. 退化状态可解释性（rv-3，报告见 `rv-3-degraded-states.json`）

非法 suite recipe / 评审预设无 judge 形态（无法禁用工具，创建 run 400「不可用」）/ 候选超时 /
评审拒答 / 坏 schema / 评审超时被击杀 / 评审非零退出 / 取消 / kill -9 自愈 / 级联删除均在真实
route + SQLite 上验证：失败态不折算 0 分（SQL 注入伪 0 分后聚合 oracle 变红、还原后恢复），
正式 Issue/PR/生命周期账本逐表不变，重启后 run 标 `interrupted` 且候选 Agent 调用数不变
（无自动重跑）。

### 执行者已替你核对过什么

- 评审隔离靠 agent 的 `judge` 形态而不是提示词：实测在空目录里让评审读目录外 `secret.txt`，
  默认 `generate` 形态的 claude / codebuddy / qoder 会泄露内容，加 `--tools ""` 后均读不到；
  codex 的 `--sandbox read-only` 只防写不防读，所以 codex / kimi / opencode 不声明 `judge`。
- 所有 rv 证据在**无任何 API key、无 provider SDK** 的机器上采集；评审边界以 fixture 进程
  做真实进程调用计数，已如实披露，未把 fixture 称为 live。
- 每次 rv 都先跑负控（badref 400、中位数敏感、拒绝零外发、篡改 409、伪 0 分检出、窄屏溢出
  注入）确认 oracle 会变红，再跑绿。
- 临时 fixture suite（`rv284-*.yaml`）与隔离 worktree/暂存 payload 在运行结束后均已清理，
  不进入代码 diff。

## 评审改走执行器后的证据重采集（2026-10-15）

初稿的评审走独立 provider（`create_chat_model` + mock HTTP 服务），整体改为复用 Agent 执行器后，
三个 rv 剧本连同各自负控在**同一工作树**完整重跑并全部转绿（`RV1_GREEN` / `RV2_GREEN` /
`RV3_GREEN: true`），截图、run JSON、fresh SQLite 读取、tree stamps 与退化状态报告同批再生成：

| 捕获 | 关键标记 |
|---|---|
| `rv-1-final-tree-run.txt` | `JUDGE_INVOCATIONS_AT_START: 0`、`JUDGE_INVOCATIONS_AFTER_APPROVE: 8`、`JUDGE_CWD_EMPTY_TEMP_DIR: True`、`SCORE_COUNT: 8` 全 valid、`AGENT_INVOCATIONS: 4`、`WORKTREES_PURGED: True` |
| `rv-1-narrow-rereview-run.txt` | 390×844 `NARROW_NO_PAGE_OVERFLOW: True`、溢出注入负控先红后绿 |
| `rv-2-final-tree-run.txt` | 页面拒绝后 `REQUEST_COUNT_AFTER_PAGE_DENY: 0`、过期 hash `STALE_APPROVE_STATUS: 409`、还原后批准 `JUDGE_INVOCATIONS_AFTER_APPROVE_RUN2: 8`、`JUDGE_PROMPT_SHA256_EQUALS_APPROVED_PAYLOADS: True` |
| `rv-3-final-tree-run.txt` | `ENV1/ENV2_SCENARIOS` 全覆盖、伪 0 分注入被 oracle 检出后还原、`RV3_VERDICT: PASS` |
| `rv-1-playwright-rereview-run.txt` | 真实 `/app/evaluations/` 页面 spec 3 passed（页面评审清单改为 agent/model 展示后复跑） |

门禁同批复跑结果见 `evidence.json` 的 `recollected_after_executor_judge.gates`。

**未覆盖与边界披露**：真实 claude / codebuddy / qoder 评审未在本证据中执行（用户 opt-in）；
codebuddy / qoder 在工具被拒时偶尔会输出工具调用文本，会按 rubric schema 校验落 `invalid`
而不是 0 分，fixture 未复现该行为，由 `parse_judge_response` 既有的非法响应用例覆盖。
tree 绑定见 `rv-1-tree-stamps.txt`。
