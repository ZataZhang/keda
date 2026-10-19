# rv-1..rv-4 验收证据报告 — Agent 可用性引导与自定义 Agent 注册

PRD：`tasks/pending/P1-FEAT-20261020-011007-agent-availability-and-custom-registry.md`
验证树：`git rev-parse HEAD = c0e3a084efd2fee4414915d23d129842a2c031f3`（交付改动以工作树形式叠加在该树上）
执行时间：首轮 2026-10-19T21:07Z（四条 rv）/ 21:29Z（rv-4 的 e2e 复跑）；**最终树重跑 21:48:41Z–21:49:22Z**（rv-1..rv-4 全部 exit 0，`evidence.json` 的 stdout 断言逐条命中，配置 sha256 与首轮逐字节相同）。
重跑原因：源码文件 mtime 出现于首轮取证之后（含 rv-4 所服务的前端包），因此按 §9「任何 seed / 门禁 / 注册路径改动后重跑对应 oracle」重取全套证据，本目录内的产物与两张截图均来自重跑后的最终树。

## 人审导航 / Human Review Navigation

| 你要看的决定 | 直接看这里 | 约 10 秒自检 |
|---|---|---|
| 决策一：默认模板不再携带个人预设；干净机器只写入真实可用的那个 Agent | 下面「rv-1」的 `负控后 sha256` / `写入后 sha256` 两行，以及「写入后的机器级配置」五行 | 配置里只有 `implementation = "claude"`，没有 `[agent_runner.presets.*]`；负控那行 sha 与骨架相同 |
| 决策二：阶段绑到未安装的 Agent 时，在领取 Issue 之前停下，且回退候选不换人 | 下面「rv-2」的报错原文一行 + `rv-2-issue-unchanged.json` 的 `"unchanged": true` | 报错同时点名 阶段/预设/agent/bin/`kc agent setup`；Issue #291 标签 3→3、评论 0→0 |
| 决策三：未内置 Agent 靠显式参数注册，非法输入被拒且不写盘 | 下面「rv-3」的注册块五行 + 三条负控的 exit code | `args = ["--print"]` 是你给的那个参数；三条负控全是 exit code 2 |
| 设置页把「未安装」和「可用」分开呈现 | 本文件内嵌的两张截图（矩阵页 + 执行器选择器） | 「实现」行写着 `未安装（PATH 中找不到 codebuddy）`，claude 行只有路径 |

真实浏览器截图（验证层级：**真实入口** — `kc console` 起真实面板 + 真实机器级配置 + 真实 PATH，无组件预览、无注入状态）。PRD 明确判定本次不需要单独的低保真原型（唯一前端变化是现有生命周期页增加可用性标记），因此下面这组截图本身就是实现呈递物，其层级在此标注。

![生命周期设置页：codebuddy 未安装标记](./rv-4-settings-availability.png)

![执行器选择器：候选项点名「未安装」](./rv-4-selector-availability.png)

## 四条 rv 的结果

| id | 真实入口 | 结果 | 关键产物 |
|---|---|---|---|
| rv-1 | `kc agent setup` / `kc agent list` / 真实 TTY 首启提示（临时 HOME + 只有 claude stub 的 PATH） | ✅ | `rv-1-setup.txt`、`rv-1-config-diff.txt`、`rv-1-first-run-hint.txt` |
| rv-2 | `kc run --issue 291 --dry-run --json`（仓库层把 implementation 绑到未安装的 codebuddy） | ✅ | `rv-2-blocked.txt`、`rv-2-issue-unchanged.json` |
| rv-3 | `kc agent add myagent --bin … --arg=--print --write --yes` + 新进程 `doctor` / `list` | ✅ | `rv-3-register.txt`、`rv-3-doctor.json`、`rv-3-list.json` |
| rv-4 | `kc console` → `/app/settings/lifecycle/`（真实浏览器逐行核对）+ `just e2e tests/workflows/lifecycle-settings.spec.ts` | ✅ | `rv-4-settings-page.txt`、两张 PNG、`rv-4-e2e-spec.txt` |

四条 rv 都在同一套隔离沙箱里跑：`env -i HOME=<临时 home> PATH=<stub 目录> KEDACODE_CONFIG=<临时配置>` + `uv run --no-sync`，因此「未安装」是这台机器的真实状态而不是测试常量；所有配置写入都由真实 CLI 完成（`kc agent setup` / `preset set` / `lifecycle set` / `fallback candidate add` / `agent add`），脚本没有手改过 TOML。逐步口径与负面控制矩阵在 `….verification-plan.md`。

恢复轮（测试命名族修复 + `kc config migrate --dry-run` 缺陷修复）之后，四条命令已在最终树整体重跑：`2026-10-19T22:11:20Z`–`22:12:27Z`，四条 exit 0，两张 PNG 与全部产物均为重跑产物；`evidence.json` 的 stdout 断言按 runner 重放口径复验为 `MANIFEST_OK: 4 items`，rv-1 / rv-3 的配置 sha256（骨架 `842049520cf3…`、setup 后 `2aafa48b67f3…`）与上一轮逐字节相同，rv-2 结论行仍为「正控 exit=3 / 负控① exit=10 / 负控② exit=3，Issue #291 标签 3→3、评论 0→0」。同轮 `just test all` = 4580 passed / 0 failed、`just e2e tests/workflows/lifecycle-settings.spec.ts` = 8 passed、`SKIP=check-test-flag uv run pre-commit run --all-files` 全钩子通过。文档与证据文件定稿后又完整重跑一次 `just test all`（同样 4580 passed / 1 skipped / 0 failed），`.last_tested_commit` 与 `.last_linted_commit` 两个标记随之刷新，独立的 `just lint --full` 亦返回「just test 标记有效，允许提交」。

### rv-1 · 干净环境引导只写入真实可用 Agent

```
负控（--agent codebuddy）exit code: 3；负控后 sha256: 842049520cf3bc3db32e9b73ba9cd0ea21c32542a70b18a589d1b45e7dcc83b5  ← 与骨架相同
正控（--agent claude）  exit code: 0；写入后 sha256: 2aafa48b67f38786c5269932dab94ae242327d95b6dfa551e2068666454dce16
写入后的机器级配置只有：
    [agent_runner]
    [agent_runner.lifecycle_agents]
    implementation = "claude"
presets: （无）  lifecycle_presets: （无）
本机可用 agent（CLI 自己解析）: ['claude']
第二次 setup：already_satisfied=true，sha256 不变
agent 提示出现次数（期望 1）: 1
```

新进程读回的 `lifecycle-settings?scope=global` 里，七个注册 Agent 的 `agent_availability` 只有 `claude` 带 `bin_path`（指向本次沙箱 stub），其余 `available=false / bin_path=null` —— 可用性事实与 `kc agent list --json` 同源。

### rv-2 · 缺失 Agent 在 claim 前拦截，回退候选不换人

```
exit code: 3
stage 'implementation' (preset 'qoder-qwen3_8-flash_max') resolves to agent 'codebuddy' (bin 'codebuddy'),
which is not available on PATH. Install 'codebuddy' or run `kc agent setup`;
`kc agent setup --fix` removes global-layer bindings that point at unavailable agents.

Issue #291 标签 3→3 / 评论 0→0   "unchanged": true
git worktree list 摘要 前=后 eb1a70bc43b1a68c…
attempt 账本记录数: 前 0 / 后 0

负控① 同一预设改绑可用 claude → exit code 10（进入 dry-run 计划：would process Issue #291 (ready) with claude）
负控② 主 Agent 仍缺失 + 显式回退候选 claude → 仍然 exit code 3，attempt 账本 0 条（没有替代 Agent 的执行记录）
```

负控①证明这不是无条件阻断；负控②证明回退阶梯不会把「没装」掩盖成「换人」。整条链路只动 Issue #291 的**预览**，#292 未被读写。

### rv-3 · 自定义 Agent 显式注册

```
不加 --write：written=false，配置字节不变
注册块（add 自己生成）：
    [agent_runner.agents.myagent]
    bin = "…/tmp.Liqoeld2KL/bin/myagent"
    label = "agent/myagent"

    [agent_runner.agents.myagent.profiles.run]
    args = ["--print"]
    prompt_delivery = "argv_tail"
    output_protocol = "plain"

新进程 list: available=True bin_path=…/bin/myagent  声明用途=['run']
新进程 doctor argv: ["…/bin/myagent", "--print", "golden-prompt"]   ← 与本次输入逐段一致，没有代猜旗标
doctor --lifecycle implementation → agent=myagent（阶段选择里可选）
负控 重名 / 未注册协议 / 缺 --prompt-flag → 三条均 exit code 2，前后 sha256 相同 cf6982447a55ae8680716869c951f22cdae810614b0b4efff3afbf53fde75945
```

### rv-4 · 设置页的可用性呈现

真实面板（`kc console --no-browser`，日志 `kc console listening on http://127.0.0.1:63971/`）打开 `/app/settings/lifecycle/`（HTTP 200），逐行核对页面渲染与后端 `agent_availability`：

```
implementation = codebuddy → 「未安装（PATH 中找不到 codebuddy）」
fix            = codebuddy → 同上
review         = codex     → 「未安装（PATH 中找不到 codex）」
closeout / verifier / supervisor / planner / content_generation / deliberate = claude → 只有路径标记，无未安装标记
选择器候选：codex · 未安装 / claude / kimi · 未安装 / pi · 未安装 / codebuddy · 未安装 / qoder · 未安装 / opencode · 未安装
```

自动化部分 `just e2e tests/workflows/lifecycle-settings.spec.ts`（真实后端 + 真实 frontend-public）：**8 passed**，含新增的可用性标记用例（页面标记逐行对照后端下发事实，前端漏接即失败）。

## 执行者已替你核对过什么（自动档）

这些不需要你看，失败时才会浮上来：

- PRD 新增的六个测试文件 96 passed；`tests/test_agent_doctor_cli.py` 21 passed（内置 Agent 的 `doctor --all-profiles --json` 与改动前**逐字节一致**的黄金快照在内）。
- Agent / 生命周期 / loop 相关 18 个测试文件批量：399 passed，1 个失败为既有基线（见下）。
- `tests/guards/` 313 passed / 1 skipped；前端 typecheck 干净；`SKIP=check-test-flag uv run pre-commit run --all-files` 全部钩子通过（runner 的 pre-commit 口径）。
- 机器可核对的 oracle 清单：`evidence.json` 四条命令 + stdout 断言，已用 `scripts/check-manifest-assertions.py` 按 runner 的重放口径（`bash -lc` + 字面子串）预校验通过；四个脚本本身可重复执行，产物连同 `git rev-parse HEAD` 与每次的配置 sha256 一起落盘。

## 已知偏差、基线红与残留限制

1. rv-2 期望里的「退出码 10」需要 `--dry-run --json`；表格模式 dry-run 按既有语义退出 0。
2. rv-3 的 `kc agent doctor myagent --all-profiles --json` 与 PRD 文本略有不同：未内置 Agent 没有多 profile，改用 `doctor myagent --json` + `doctor --lifecycle implementation --json` 取证。
3. rv-4 依赖 `just console-sync` 产出的面板静态产物（`src/backend/api/static/console` 被 gitignore），复跑前需要先同步。
4. 仓库模板 `config.toml` 删掉个人预设后，仓库层 `.kedacode.toml` 仍把 `implementation` 绑在 `qoder-qwen3_8-flash_max` 上。实测：这台机器上的维护者级 `~/.kedacode/config.toml` 定义了该预设所以照常解析；在一台没有该定义的新机器上，解析**优雅降级**（`preset_name=null`，`effective_agent` 回落到 legacy/builtin，退出码 0，不报错）。维护者仓库层绑定与模板定义就此分离，属于已知状态而非本次修复项。
5. `loop_fire` 路径（`src/backend/core/use_cases/loop_fire.py`）仍用内置 registry 解析阶段 Agent，因此注册了自定义 Agent 的 loop 能建不能发射 —— 已作为已知限制记录在 PRD Change Log，不在本次交付范围内悄悄扩scope。
6. ~~`just test` 当前有 33 个失败~~ **已在本轮修复并转绿**：这 33 项全部是 HEAD `27ad6b57 refactor(cli): remove IAR runtime compatibility` 改名后未同步的用例（`_parse_unmanaged_kind(('uv','run','iar',…))`、`.iar.toml` fixture、`IAR_IDEA_INBOX_INBOUND_SECRET`、`resolve_prd_skill_path` 期望 `.iar/skills` 等），已在 `c0e3a084` 的隔离 worktree 逐条复现为同样的 33 red，再把用例改成当前产品命名后于最终树跑绿：`just test all` = **4580 passed / 1 skipped / 0 failed**（`-4` 项是刻意合并的 `[.iar]` 参数化）。修复过程中另外发现并修掉一处真实缺陷：仓库仍只有旧名 `.iar.toml` 时 `kc config migrate --dry-run` 会误报「仓库未初始化」并以 3 退出，现在预演读改名前的文件，退出 0 并同时给出改名与钉子清理计划。两条均登记在 PRD Change Log，未跳过或弱化任何门禁。
7. e2e 包另有两处既有类型检查错误（`tests/smoke/idea-inbox.spec.ts:123`、`tests/workflows/console-evaluations.no-auth.spec.ts:42`）与 `frontend-public` 的 2 个既有 eslint 错误，均与本次改动无关。
8. `kc agent doctor --all-profiles`（不带 agent 名）在 main 上就报 `'NoneType' object is not iterable`，本次未改动该语义（PRD 要求内置 Agent 的黄金快照逐字节一致）。
9. 转录用例的一次红：`repo-agent-gear-*` 对 registry 里**停用**的仓库照样可点，点到 `transmaster`（`~/.kedacode/config.toml` 中 `enabled = false`，2026-06-11 起停用）得到「仓库 'transmaster' 不存在或未启用」而不是矩阵。该用例的页头前提本来就是「已启用的受管理仓库」，已改为先读 `/api/v1/agent-runner/repositories` 选 enabled 的仓库，无启用仓库则 skip；生产代码未动，改后 8 passed。

## 文件索引

| 文件 | 内容 |
|---|---|
| `scripts/rv-{1,2,3,4}-*.sh`（+ `rv-4-settings-page.cjs`） | 四条 oracle 的可重复执行脚本 |
| `rv-1-setup.txt` / `rv-1-config-diff.txt` / `rv-1-first-run-hint.txt` / `rv-1-*.json` | rv-1 终端记录、配置差异与哈希、首启提示、setup/list/lifecycle JSON |
| `rv-2-blocked.txt` / `rv-2-issue-unchanged.json` / `rv-2-run-*.json|.err` / `rv-2-*.json` | rv-2 报错与副作用核对、两条负控、回退候选清单 |
| `rv-3-register.txt` / `rv-3-doctor.json` / `rv-3-list.json` / `rv-3-negative-*.json` / `rv-3-protocols.txt` | rv-3 注册块、doctor/list、三条拒绝负控、已注册协议事实源 |
| `rv-4-settings-page.txt` / `rv-4-page-facts.json` / 两张 PNG / `rv-4-e2e-spec.txt` | rv-4 页面逐行核对、事实 JSON、截图、e2e 结果 |
| `….verification-plan.md` | 逐步口径、负面控制矩阵、偏差登记 |
| `evidence.json` | runner 机器核对用的命令 + stdout 断言清单 |
