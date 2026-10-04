# Verifier Report — P1-FEAT-20260930-212702-agent-token-usage-stats

**verifier 结论：PASS-with-notes**（无 HIGH 问题；1 项 MEDIUM、3 项 LOW、2 项备案观察）

- 核对 HEAD：`44f685c8b58c1587a7ea64de3d35638323fa2b6c`（分支 `feat/agent-token-usage-stats`）
- 冻结凭证：`git diff HEAD -- src tests frontend-public docs | shasum -a 256` = `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空树哈希，工作区与 HEAD 一致）✅；`git status --porcelain` 干净 ✅
- 变更范围：`git show --stat HEAD` = 28 文件 +2303/−26，与 PRD 陈述一致；`tests/guards/` 零改动 ✅

## 执行过的命令

```
git rev-parse HEAD && git branch --show-current && git status --porcelain
git diff HEAD -- src tests frontend-public docs | shasum -a 256
git show --stat HEAD / git show --name-only --format="" HEAD | sort
uv run pytest tests/test_agent_stream_usage.py tests/test_agent_token_stats.py tests/test_agent_token_usage_flow.py tests/test_prd_lifecycle.py -o addopts="" -q   # 46 passed (1.21s)
uv run pytest tests/test_process_runner.py -o addopts="" -q            # 46 passed (13.98s)
uv run pytest tests/test_lifecycle_agent_routing.py -o addopts="" -q   # 22 passed (0.30s)
uv run python hooks/shared/check_max_file_lines.py --max-lines 1000 <全部 24 个代码文件>  # exit 0，无越线
git show HEAD -- <逐文件>   # 代码级抽查（process_runner / lifecycle / token_stats / execution_loop / orchestration_runtime / review_once / run_verifier_agent / pr_supervisor / roadmap.py / engines 三文件 / issue_handlers / orchestrate / run_agent_once / attempt）
rg -n ...   # _EVENT_PHASE、前端落点、docs 同步点、engines→infrastructure 依赖先例
```

## 逐 rv 复核

### rv-1 全链路（tests/test_agent_token_usage_flow.py::test_fake_agent_usage_reaches_ledger_and_stats）— ✅

实际观察：harness 在 tmp_path 真实 `git init` + seed commit；假 agent 脚本按 claude stream-json 信封输出 `result` 事件（usage 固定 1200/340/800/120）；经 `BUILTIN_AGENT_SPECS["claude"]` 替换 run profile（保留真 `CLAUDE_STREAM_JSON_PROTOCOL_ID`）后调用**真实** `run_agent_until_committed` + **真实** `SubprocessRunner()`。断言 fresh 新开 `SqliteConsoleStore` 读回 attempt detail `token_usage == _FIXED_USAGE`（逐字段），并经 `build_prd_lifecycle_stats` 验证 implement 流 1200/340/800/120、total=2460、by_agent claude=1。

- 不是"直插 store 伪造"：数据唯一来源是假 agent 子进程 stdout，必须经 StreamUsageCollector→CommandResult→AttemptResult 才能出现在事件 detail 里；落账发生在 `on_attempt_recorded` 回调（生产编排层的同一挂接点），detail 由生产唯一事实源 `build_attempt_event_detail` 构造（`agent_runner_orchestration_runtime.py` 的 `_on_attempt_recorded` 已改为调用同一函数，diff 已核对）。
- 执行循环内被 monkeypatch 的仅是与断言无关的门禁（run_verification/has_changes/ensure_*/commit_requested_changes），符合 harness 既有模式；Phase 1 agent 调用、attempt 记录、账本写入全部真实。
- 断言可失败：采集断链 → detail 无 token_usage 或数值不符 → 红。

### rv-2 解析容错（tests/test_agent_stream_usage.py + tests/test_process_runner.py）— ✅

实际观察：四字段提取、缺失→None、字符串畸形→None、部分字段补 0、bool/负数显式排除（`isinstance(raw_value, bool)` 先于 int 判断）、非 result 事件/纯文本/坏 JSON 忽略、`"result" not in line` 子串预检且不误伤合法行；收集器保留最后一个 usage、无 usage 保持 None；plain 路径 stderr 交错不影响解析；`SubprocessRunner.run` claude 流式路径赋值正确、plain 路径 None、**output_protocol=None 的普通命令即使输出 result JSON 也不解析**（test_generic_command_is_not_parsed）。全部绿（两文件共 92 用例）。

### rv-3 相位/时长不污染（tests/test_prd_lifecycle.py 新增）— ✅

实际观察：`classify_durations` 在事件序列中段（10:03/10:08）插入两条 AGENT_TOKEN_USAGE 前后 breakdown 逐位相等（`==` 断言）且 end_to_end=840s 不变；`derive_current_phase` 在观测事件后仍取前序阶段事件（executing），全观测 run 按终态降级（failed）。实现侧 `agent_runner_lifecycle.py` 以 `_OBSERVATION_ONLY_EVENT_TYPE_VALUES` 在两处过滤，`AGENT_TOKEN_USAGE` 刻意不进 `_EVENT_PHASE`（rg 确认仅 3 处引用：枚举定义、过滤集、注释）。断言可失败：若未过滤，插入事件会改变 waiting/execution 切段或把阶段拉成 none → 红。

### rv-4 聚合与端点（tests/test_agent_token_stats.py + test_prd_lifecycle.py）— ✅（见 LOW-1）

实际观察：attempt 族归 implement、观测事件按 detail.flow 分组、缺 usage 排除（by_flow/by_agent 均空）、畸形（字符串/负数/bool）与损坏 detail_json 容错、total=四项之和、空窗口零值结构；`build_prd_lifecycle_stats` 以 `window_events` 汇总；HTTP 端点断言 `payload["token_usage"]["by_flow"]["implement"]["input_tokens"]==10`、total=12（dataclass 序列化自动透出，端点代码零改动）。全部绿。

### rv-5 真实入口截图（reviewer: human）— 不在本次 verifier 范围

证据目录已有 rv-5 三张截图 + rv-report.md（orphan 分支发布口径见 PRD 9.1）；人工核验待需求方完成。

### rv-6 缺失降级负控（tests/test_agent_token_usage_flow.py 两个负例）— ✅

实际观察：无 usage（None）与畸形 usage（"oops"）两组 fixture 各走一遍真实执行循环：attempt 均正常完成（各恰有 1 条 attempt 事件）、detail 无 `token_usage` 键。**负控真能红**：单元层 `test_malformed_usage_string_returns_none` 直接调 `extract_usage_event`，实现若抛异常即红；e2e 层若采集抛 TypeError，该异常不在 Phase 1 的 `except (RuntimeError, OSError, CalledProcessError, TimeoutExpired)` 捕获集内会直接炸出测试，即使被下游吞掉也会因 `max_recovery_attempts=0` 走 `MaxRetriesExceededError` → 红；"detail 出现非 None token_usage"模式被 `"token_usage" not in detail` 断言覆盖。

## 守卫与门禁抽查

- `tests/test_lifecycle_agent_routing.py` AST 守卫 22 passed ✅（发射点均内联 `resolve_lifecycle_agent`，`_attempt_delivery_closeout` 处注释明确说明重解析以保持守卫约定形态）。
- 本 commit 无 `tests/guards/` 改动 ✅。
- 行数红线：对全部 24 个触碰代码文件跑 `check_max_file_lines.py --max-lines 1000` exit 0 ✅。

## 代码级抽查（对照四层依赖与 FR）

- **采集层** `agent_stream_usage.py`：非 dict/缺字段/bool/负数/非 result 行/坏 JSON 一律 None 不抛异常（收集器另有防御性 `except Exception` 兜底）；plain 路径有 `"result" not in line` 预检 ✅。
- **process_runner.py**：`usage_collector.observe_line(output_line)` 位于 `renderer.render_line(output_line)` **之前**（run_filtered_claude_stream stdout 循环内）✅；claude 流式失败后不回退 plain 重解析（避免对渲染后文本误解析，符合 D-02）；`output_protocol=None` 不解析 ✅。
- **engines 三文件**（PRD Impact Tree 清单外扩展）：claude_stream_json/pi_json_lines 协议路由执行器与 protocol_routing_runner 挂同一收集器、向内层传 `output_protocol` 区分 agent 调用与普通命令——方向与 PRD 一致（PRD 明示清单非穷尽），engines→infrastructure 为仓内既有依赖模式（5 个先例文件）✅。
- **lifecycle**：AGENT_TOKEN_USAGE 不在 `_EVENT_PHASE`（phase=NONE 缺省）且被时长/阶段双过滤 ✅；`build_attempt_event_detail` 为 attempt detail 唯一事实源，生产 `_on_attempt_recorded` 与测试共用 ✅。
- **聚合** `agent_runner_token_stats.py`：total_tokens = 四项之和（含缓存读写，D-07 口径）；缺失/畸形/损坏 detail 排除；bool 显式排除 ✅。
- **FR-4 发射点**：verify（run_verifier_agent 回填 ValidationVerdict.agent/token_usage 后经 execution loop `_emit_agent_usage`）、fix/closeout（execution loop 两处）、supervise（review_once `_record_agent_usage_event`，supervisor 守卫改写后 `replace(action_result, token_usage=...)` 统一回填）四类 flow 齐备；回调失败仅 warning 不阻断（`_emit_agent_usage` try/except）✅。
- **§12 收窄**：`agent_review.py`/`pr_supervisor_repair.py`/`agent_runner_worktree_branch.py`/`agent_runner_verification_recovery.py`/`agent_runner_closeout.py` 确实不在 28 文件清单中，与 PRD 实施期修订说明一致 ✅。
- **前端三落点**：types.ts `PrdLifecycleStats.token_usage` + TokenUsageStats/Totals；stats/page.tsx `data-testid="stats-token-usage"` + `cacheHitRate`（命中÷输入侧，无缓存数据返回 null）；prd-lifecycle-view.tsx attempt 与观测事件行 `summarizeTokenUsage` 渲染、缺失降级 ✅。

## 问题分级

### HIGH（验收口径未满足或断言无法失败）

无。

### MEDIUM

- **M-1｜FR-6 字面口径在 roadmap 单 PRD 端点未以聚合字段满足**：聚合 `token_usage` 只挂在 `PrdLifecycleStats`（console stats 端点）；`GET /agent-runner/roadmap/prds/{path}/lifecycle` 返回的 `PrdLifecycleDetail` 无聚合汇总字段，token 数据仅以 `events[].detail.token_usage` 逐事件透传（`PrdLifecycleEventView.detail: dict` 已核）。根因是 PRD 自身不一致：§7 step5 称"两个既有读端点自动透出"，但两端点本就返回不同 DTO，该机制不可能自动成立；rv-4 与 §9.2 验收项均只要求 stats 端点。两处展示面（FR-7 执行过程、FR-8 Stats 页）功能完整，前端无消费方需要 roadmap 端点聚合。建议 Final Reconciliation 时修订 FR-6 措辞（或后续给 PrdLifecycleDetail 补聚合字段）。

### LOW

- **L-1**：rv-4 real_entry 列出 `tests/test_agent_runner_console_api.py`，但该文件无任何 token 断言；端点形状断言实际位于 `test_prd_lifecycle.py::test_stats_endpoint_transparently_exposes_token_usage`。证据等效，real_entry 路径陈述与实际不符。
- **L-2**：§9.2 Documentation Acceptance 提及 `docs/architecture/system-design.md` 同步；实际仅更新 `docs/guides/agent-runner.md`（事件闭集、口径、stats 字段，rg 确认 L3237-3267）。未新增页面故 mkdocs.yml 无需改，system-design.md 无事件闭集/字段枚举需同步——判定可接受，但 9.2 项措辞与实际动作不完全对应。
- **L-3**：§9.2 Frontend Acceptance 称 `frontend-public/lib/api/console.ts` 类型含 token_usage/token_totals；实际类型定义在 `lib/api/types.ts`，console.ts 经 `import type { ... PrdLifecycleStats ... } from "./types"` 传递（类型可用但非定义于 console.ts）；且字段名为 `token_usage` 而非 PRD §3/§7 出现过的 `token_totals`（PRD 两处自我不一致，实现取了 Change Impact Tree 的 `token_usage`）。

### 备案观察（不计级）

- rv-1 的 `on_attempt_recorded` 为测试侧闭包（复刻生产 `_on_attempt_recorded` 的构造+落账调用）；生产编排层接线本身由"共用 build_attempt_event_detail"约束保证，若生产侧 detail 构造漂移，本测试不直接覆盖（有 rg/AST 守卫与 builder 单一事实源缓解）。
- `StreamUsageCollector.observe_line` 的防御性 `except Exception` 理论上可掩盖提取函数抛错使 rv-6 e2e 变绿，但单元层直接断言 `extract_usage_event` 容错，组合覆盖完整。

## 结论

28 文件交付与 PRD §6/§7/§10/§12 的陈述在代码、测试、门禁三个层面均经独立核实一致；rv-1/2/3/4/6 全部实跑绿且断言具备可失败性；四层依赖、行数红线、AST 守卫、guards 零改动均合规。唯一实质偏差为 M-1（FR-6 措辞 vs roadmap 端点实现，功能无缺口），不构成验收口径未满足。

verifier-verdict: PASS-with-notes
