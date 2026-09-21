# Verifier 报告：扩展内置 agent 覆盖（codebuddy / qoder / opencode）

- 复核者：独立 verifier agent（无本次实施上下文，只读权限）
- 复核时间：2026-09-20
- 冻结凭证：`git diff HEAD -- src tests | shasum -a 256` = `2665f1d2ef8ba27c07ba1d7aeef7d0a3a129e5b31f86d780e627daadc0c66f51` @ HEAD `452bfaae`
- 结论：**PASS-with-caveats**（1 major + 4 minor，均已在实施侧整改，见下）

## 冻结校验

`git rev-parse HEAD` = `452bfaae736c6ef7e031329419e9292ed8077d01`；凭证哈希精确匹配，复核期间 `src`/`tests` 未被改动。`src/` 的 diff 恰好是 `agent_spec.py`（+172 行纯数据）与 `factory_config_builder.py`（13 行合并判断），`AgentSpec` / `AgentProfileSpec` 的字段行零改动。

## oracle 结果

| id | 结果 | 依据 |
|---|---|---|
| rv-1 | PASS | `tests/test_agent_spec_config.py` 18 passed |
| rv-2 | PASS | `tests/test_agent_invocation_golden.py` 34 passed；既有四条 argv 快照是**纯新增** diff，未被改写 |
| rv-3 | PASS | `iar agent list` 顺序正确；`doctor opencode --all-profiles` 退出 1 并指名缺 `generate` |
| rv-4 | PASS（oracle 命令错靶，已修） | 原命令 `-k unknown_profile` 命中既有测试；按新用例名运行通过 |
| rv-5 | PASS | `codebuddy` 命中只剩"已注册"语境；两文件 38 passed |
| rv-6 | PASS | 派生名 `codebuddy` / `qoder-cn` / `config/opencode` 正确 |
| rv-7 | PASS | 两张 PNG 确实显示下拉含三个新 agent；txt 显示两层各 8 候选与写回落盘 |
| rv-8 | PASS（措辞过强） | 枚举已更新；但标签表连 pi 都漏列，见 minor #4 |
| rv-9 | PASS + 独立复现修复前失败 | 修复前逻辑下同断言失败，错误信息与 PRD 一致 |
| rv-10 | pytest PASS / lint 首轮 FAIL | `2394 passed / 0 failed`；`just lint --full` 首轮因行尾空格失败 |

## 尝试过的证伪（均未成功）

- **既有快照是否被偷改以迎合新行为**：diff 为纯新增，`test_agent_invocation_golden.py` 只有已注册名元组变化。
- **合并修正是否悄悄影响既有 agent**：四个内置 agent 都声明齐全四用途，修复前后黄金快照完全一致；受影响的只有"内置 spec 本就缺某用途"这一种形状。
- **对抗性合并用例**：部分声明的新 agent（不变）、显式空段（仍报错）、配置与内置都有该用途（正常合并）、两侧都缺（本次唯一有意变化）。
- **`opencode` 是否其实有只读开关**：`opencode run --help` 只有 `--dangerously-skip-permissions`，无沙箱。
- **`qoder` 的 flag 与 schema**：`qodercn --help` 确无 `--verbose`/`--include-partial-messages`；`-o bogus` 报出 `text/json/stream-json`；bundle 内含 `type:"stream_event"/"assistant"/"result"` 与 `text_delta`/`message_stop`，与 `process_runner.py` 的渲染器匹配。
- **出厂块是否是"谎言"**：守卫逐字段比对标量与 profile 字段，round-trip 重建注册表并断言等于 `BUILTIN_AGENT_SPECS`；`opencode` 的两侧都缺 `generate` 与 `project_skills_dir`。
- **前端/README/架构文档里是否有硬编码 agent 列表**：未发现。

## Findings

| # | 严重度 | 内容 | 处置 |
|---|---|---|---|
| 1 | major | 冻结树过不了 `just lint --full`：证据文件 `rv-3-agent-doctor.txt` 4 行带行尾空格，`trim trailing whitespace` 改文件即判失败，`CI=true just test all` 也会在此中止。与 §9.2 声称的"lint 通过"矛盾 | 已清理行尾空格、重新全量测试刷新标记，lint 真通过 |
| 2 | minor | §9.2 声称容器认证副作用"已在 `agent-runner.md` 披露"不成立（那里仍只列三个目录且漏 `pi/agent`） | 已在该文档补披露（含 `pi/agent` 与 `config/opencode`） |
| 3 | minor | rv-4 的 oracle 命令打中的是既有测试而非新增用例 | 已改 real_entry 为 `-k opencode_has_no_generate_profile` |
| 4 | minor | `agent-runner.md` 的「Planner 安全」段称"仅 codex 安全，claude 和 kimi 会 fail fast"，与门禁实现（只读声明的 `read_only`）相反；§9.2 的只读勾选项也把声明说成机制 | 已按实现改写该段；§9.2 改为"声明范围与实现一致"；§12 明确 `read_only` 是声明非运行时强制 |
| 5 | nit | 「工具路由」标签表只列 codex/claude/kimi（连 pi 都漏），使 rv-8 的"每一处都含三个新名字"不成立于字面 | 已补齐 pi 与三个新 agent |
| 6 | nit | qoder 复用 `claude-stream-json` 仅为静态验证（参数探针 + bundle schema），PRD 措辞偏强 | 已在 §12 显式写"未做真实流式运行"，并说明失败表现 |

## 披露缺口（复核时指出，均已修复）

- lint 门禁失败未披露 → 已修并记入 §9.2 与证据报告 §5。
- 容器认证文档披露位置不实 → 已修。
- 分级复核：全部标 R0/R1 可接受（改动是纯数据 + 单函数单条件），但 qoder 的协议复用属**外部 CLI 契约**假设，PRD 应明说"未做真实流式运行" → 已补。

## 剩余不确定性

- 三个新 agent 均未跑真实任务（额度/副作用限制）；"与 claude 同构"依据是 help 文本与 bundle 字符串。
- 2 条端口失败在复核时无法复现（端口已释放），该结论由"计数精确吻合 + 失败机制是固定端口测试 + 主仓库 HEAD 同样失败"支撑，而非直接观测到失败。
- rv-7 用主仓库预构建前端 + worktree 后端，已如实披露；不证明任何前端源码改动（本 PRD 无前端源码改动）。
