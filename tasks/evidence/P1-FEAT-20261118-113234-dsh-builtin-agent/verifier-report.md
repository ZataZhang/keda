## 人审导航 / Human Review Navigation

- **结论：PASS**——PRD `§9.3` 的 rv-8 计数已从 31 改为 35，与检查脚本/证据一致；本轮未再发现阻断项。决策 B 的续传与 read-only 声明、既有 8 个 agent 回归均已复核通过。
- 本报告：`tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/verifier-report.md`
- 被复核 PRD：`tasks/pending/P1-FEAT-20261118-113234-dsh-builtin-agent.md`
- 证据目录：`tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/`
- 关键图：![rv-4 保存后：实现/修复/收尾生效 AGENT = dsh，toast 已保存](rv-4-console-saved.png)
  打开命令：`open tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/rv-4-console-saved.png`
- 复核方式：只读检查 + 重跑关键命令；除本报告外未修改任何交付文件。
- 说明：工作区仍是多会话混用树；`just test all` 在主混合树的失败归因其他会话的 toml 锁重构，不计入本交付。本交付全量证据是隔离 worktree 的 5400 passed / 1 skipped。

---

# 第五轮（最终确认）独立验证报告：dsh 内置 agent

- 复核时间：2026-11-18（Asia/Shanghai）
- 总体判定：**PASS**

## 0. 最终确认

### 0.1 上一轮唯一阻断项已修复

- PRD `§9.3` 的 rv-8 已改为 **35 条逐处枚举断言全 PASS**（原为 31）。
- `check_enumeration_sync.py` 本次重跑：**35/35 PASS，exit 0**。
- `rv-8-enumeration-sync.txt`：35 条 PASS；evidence report 两处均为 35 条。
- 上一轮其余的 rv-2 45 passed、config 注释、PRD §2 A/B 勾选、头部/Final Reconciliation/Change Log 一致性均保持修复状态。

### 0.2 §1 解读签署框

- PRD §1 的“我确认上述解读与 7 行行为样例符合预期”仍为空框，其下新增说明：12:39 聊天回复确认的是 §2 两项决策与 §9.1 呈递面，未涉及 §1 解读表，签署框留给用户补勾。
- 这是**已明确声明的流程状态**，不是隐藏的不一致；PRD 头部也把“已验收”限定在 §2/§9.1。作为非阻断项保留：若用户后续要覆盖 §1，补勾即可；当前不代勾。

## 1. 最终检查范围

- FR-1..FR-9：注册表、协议、观测、配置、文档枚举、既有默认值零改动。
- rv-1..rv-12：配置/argv/真实 dsh 探针/console 保存与写回/doctor/用量/渲染/枚举/全量/续传/read-only。
- 决策 B：`supports_resume=True`、`resume_args`、generate `read_only_enforced=True`。
- 回归：既有 8 个 agent 的 spec/argv、mcode/codex 快照。
- Human-Confirmed：聊天确认转录与 PRD 状态一致性。

## 2. FR-1..FR-9 检查表

| FR | 结果 | 依据 |
|---|---|---|
| FR-1 追加 dsh，旧 8 顺序不变 | **PASS** | 旧 8 个 `BUILTIN_AGENT_SPECS` 与 HEAD 逐字段 `asdict` 完全一致；`kc agent list` 旧 8 顺序不变，dsh 追加末尾 |
| FR-2 四用途、无 judge、fail-fast 指名 | **PASS** | doctor `--all-profiles` exit 3，输出 `agent 'dsh' has no 'judge' profile (declared: run, deliberate, generate, repl)`；无 run 回落 |
| FR-3 续传/强制只读声明 | **PASS** | `supports_resume=True`、`resume_args=("--session-id","{session_id}")`；deliberate/generate `read_only=True`，generate `read_only_enforced=True`；无 model/effort 声明 |
| FR-4 dsh-json-lines 协议 | **PASS** | 协议 id 常量/接口再导出/模块/entry point 齐全；渲染测试 19 passed |
| FR-5 step_end 用量、codex/mcode 隔离 | **PASS** | 65 passed；dsh 分支只认 `status`+`step_end`，既有 codex/mcode 用例未改 |
| FR-6 四用途 argv、stdin、权限档 | **PASS** | golden 45 passed；续传时 `--session-id <id>` 在 `-` 前；run/deliberate/repl danger-full-access+never，generate read-only+never |
| FR-7 config.toml 字段一致 | **PASS** | 18 passed；dsh 字段与代码一致；续传/只读注释已按决策 B 更新 |
| FR-8 docs/原型/skill/sync/测试枚举 | **PASS** | 35/35 检查 PASS；config 注释、agent-runner、configuration、原型、随包 skill、sync 三数组均同步；PRD §9.3 已 35 |
| FR-9 既有默认值/既有 8 个 agent 零改动 | **PASS** | 旧 8 个 spec 字段级一致；golden 既有快照零 diff；default_agent/fallback/default profile 未变 |

## 3. Oracle 检查表

| oracle | 结果 | 依据 |
|---|---|---|
| rv-1 | **PASS** | 18 passed；config 出厂块与代码逐字段一致 |
| rv-2 | **PASS** | 45 passed；dsh 四条 argv + 续传形态；既有八条零 diff |
| rv-3 | **PASS** | 真实 dsh 探针 9/9 PASS；sessionId 同源、退出码/final.reason 自洽、非法权限档 `[agent error]`、fresh state；无凭据为已知限制 |
| rv-4 | **PASS** | 真实 console：两次 PATCH 200、实现阶段绑定、保存后生效 AGENT=dsh + toast、config.toml diff、恢复后 SHA-256 一致、仓库层下拉含 dsh |
| rv-5 | **PASS** | doctor exit 3 指名 judge；doctor run exit 0；非法权限档真实 error 被渲染 |
| rv-6 | **PASS** | 65 passed；step_end 11/7/5/3 映射；codex/mcode 既有用例零 diff |
| rv-7 | **PASS** | 19 passed；text/thinking/tool_call/tool_result(error)/error；final 不重复渲染；truncated 容错 |
| rv-8 | **PASS** | 35/35 检查 PASS；证据文件/报告/PRD 均为 35 条 |
| rv-9 | **PASS（caveat）** | 隔离 worktree 5400 passed / 1 skipped + lint --full；本次 `mkdocs build --strict` exit 0；主混合树失败不归因本交付 |
| rv-10 | **SKIPPED** | 无 `DEEPSEEK_API_KEY`；PRD 标 `required_for_acceptance=false`，未伪装 PASS |
| rv-11 | **PASS（残余）** | 既有会话续传：sessionId 不变、turn=2；另以 kc 同形 argv（danger-full-access + never + `--session-id` + `-` + stdin）续传同一会话，turn=3；完整上下文仍需凭据 |
| rv-12 | **PASS（残余）** | 真实 read-only 运行；会话日志最终 `permission/preset=read-only`、`sandbox/mode=read-only`、`approval=never`；写文件被拒仍未实测 |

## 4. 决策 B 专项判断

### 4.1 read_only_enforced

- dsh generate：`--permission-mode read-only --approval-policy never` + `read_only_enforced=True`。
- `rv-12` 真实运行，会话日志最终 read-only/never；日志先出现默认 workspace-write/ask、随后被显式覆盖，证据已说明。
- **判断：达到仓库既有 flag-level 口径**（与 codex `--sandbox read-only`、pi `--no-tools` 同类：provider 沙箱/权限开关被 argv 使用且运行时 mode 被应用）。
- **残余风险：** 没有“真实写文件被拒”的负控；`read_only_enforced` 的强保证（即使提示词被忽略也写不进工作区）未被 dsh 真机证实。PRD §12 已记补验条件；决策 B 下接受，不阻断本轮 PASS，但人审应披露。

### 4.2 supports_resume

- `supports_resume=True`、`resume_args=("--session-id","{session_id}")`；golden 断言 `--session-id` 在 `-` 前。
- `rv-11`：既有会话续传 sessionId 不变、turn=2；kc 同形 argv + stdin 续传同一会话，turn=3；失败点是 MISSING_CREDENTIAL，不是续传参数错误。
- **判断：** 会话接管与精确 kc argv 形态均有真实证据，足以支撑 `supports_resume=True` 的声明。
- **残余风险：** 完整上下文续跑（真实模型调用）仍需凭据；rv-10 仍 skipped，PRD §12 已记。

### 4.3 回归

- 旧 8 个 `BUILTIN_AGENT_SPECS` 与 HEAD 逐字段一致；dsh 仅追加末尾。
- 定向 7 文件 **213 passed in 5.38s**；golden **45 passed**；既有八条快照零 diff；mcode/codex 用例未改。
- `kc agent list` 旧 8 顺序不变，dsh available；`kc agent doctor dsh --all-profiles` exit 3；`kc agent doctor dsh` exit 0。
- 隔离 worktree 全量证据 **5400 passed / 1 skipped + lint --full**；主混合树失败归因其他会话，未计入。

## 5. Human-Confirmed 判断

- 人类在 2026-11-18 12:39 明确回复 `1A / 2B / 确认`；Agent 转录为 §2 选项与 Human-Confirmed 记录，并在 §2 注明“Agent 仅转录”。这不是伪造，仓库既有“人工在交互会话确认后勾选”的先例，**转录可接受**。
- 当前 PRD 已一致：§2 决策一 A、决策二 B 已 `[x]`；头部为 `✅ 已验收` 并限定 §2/§9.1；Final Reconciliation 已写“决策一 A、决策二 B，Agent 仅转录”；Change Log 已写“Human-Confirmed 已按聊天回复记录”。
- PRD §1 的解读签署框仍为 `[ ]`，其下说明明确它不在 12:39 的确认范围内、留给用户补勾；PRD 头部也把已验收限定在 §2/§9.1。这不是隐藏不一致，本轮作为非阻断流程项保留。
- 如果团队要求“Human-Confirmed 只能人手动勾”，则应恢复 `[ ]`；当前采用“明确聊天确认 + Agent 仅转录”口径，可以接受。

## 6. 证据可信度

- **rv-1/2/5/6/7/8**：真实 config/注册表/CLI/解析器/文件断言；本次重跑 213 passed、45 golden、35/35 枚举。
- **rv-3**：真实 dsh 子进程 stdout 同时喂观测器与渲染器；另起真实 relay 进程核对退出码/session 独立性；无合成事件。
- **rv-4**：真实 console 保存 + 真实 PATCH + config.toml diff + 备份恢复 SHA-256 + 4 张真实截图；非前端模拟。
- **rv-9**：隔离 worktree `just test all` 5400 passed / 1 skipped + lint --full；本次 `mkdocs build --strict` exit 0。主混合树失败归因其他会话。
- **rv-11/rv-12**：真实 dsh 二进制、真实持久化会话/会话日志；rv-11 补充了 kc 同形 argv + stdin（turn=3），rv-12 说明了默认档先出现、显式档后覆盖。
- **无密钥泄漏**：rv-11 用 `/tmp`，rv-12 用 `<HOME>`；证据报告已用 `<HOME>`；`rv-4-console-saved.png` 仍显示宿主 dsh 路径（PNG 通常不入库，分发人审附件时建议脱敏）。
- **无伪造**：无 `rv-10` 冒充 PASS；无未授权的人类勾选声称。

## 7. 复跑命令与结果

```text
$ uv run pytest -o addopts='' tests/test_agent_spec_config.py \
    tests/test_agent_invocation_golden.py tests/test_dsh_output_protocol.py \
    tests/test_agent_stream_usage.py tests/test_output_protocol_registry.py \
    tests/test_container_auth.py tests/test_agent_config_consistency.py -q
213 passed in 5.38s

$ uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q
45 passed in 0.11s

$ uv run python tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/check_enumeration_sync.py
35/35 [PASS]；exit 0

$ uv run python tasks/evidence/P1-FEAT-20261118-113234-dsh-builtin-agent/scripts/probe_dsh_protocol.py
9/9 [PASS]；exit 0

$ uv run mkdocs build --strict
exit 0；Built in 4.38 seconds

# 隔离 worktree 全量证据（未变）：5400 passed / 1 skipped + lint --full
```

## 8. 残余风险

- `read_only_enforced` 只有 flag-level + 会话 sandbox/mode 证据，未验证真实写文件被拒；PRD §12 已记补验。
- 续传只验证会话接管与 kc 同形 argv/stdin（turn 2/3），完整上下文续跑仍需凭据；rv-10 仍 skipped。
- `rv-9` 全量证据是隔离 worktree 摘要；主混合树失败归因其他会话。
- PRD §1 解读签署框仍留给用户补勾；不影响 §2/§9.1 的已确认状态。

## 9. 最终判定

**PASS**。

- 上一轮唯一阻断项（PRD §9.3 rv-8 31→35）已修复并复核；本轮无新增阻断。
- FR-1..FR-9 全部满足；rv-1..rv-9、rv-11、rv-12 通过；rv-10 按 PRD skipped，未伪装。
- 既有 8 个 agent 的 spec/argv/default 零改动；无回归。
- 决策 B 的 `read_only_enforced` 与 `supports_resume` 在仓库既有 flag-level 口径下有真实证据；write-denial 与完整上下文续跑列为需凭据补验的残余风险，已在 PRD §12 披露。
