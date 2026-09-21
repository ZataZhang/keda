# 证据报告：扩展内置 agent 覆盖（codebuddy / qoder / opencode）

对应 PRD：`tasks/archive/P1-FEAT-20260920-225333-builtin-agent-coverage-codebuddy-qoder-opencode.md`
冻结凭证：`git diff HEAD -- src tests | shasum -a 256` = `2665f1d2ef8ba27c07ba1d7aeef7d0a3a129e5b31f86d780e627daadc0c66f51` @ HEAD `452bfaae`
实施日期：2026-09-20 · 实施者：codebuddy（执行锁持有方）

> 本目录下除三份 `.md` 外的产物（两张 PNG、两份 txt、`scripts/`）按仓库惯例**不进版本库**（`tasks/evidence/` 只跟踪 `.md`，与历史 43 个 md / 0 个 png / 0 个 script 一致），但都在本机同目录内，可复核。图片已就地内嵌，可直接阅读。

---

## 1. 结论

全部 oracle 通过。核心回归线（既有四个 agent 的命令行逐字节不变）成立，合并判断修正在未修改的基线上可复现失败，console 两层下拉在真实入口里确实渲染出三个新 agent 且写回真的落盘。

## 2. rv-1 / rv-2 / rv-9 / rv-4 / rv-6 / rv-5 / rv-8（机器层，verifier 复核）

```text
rv-1  uv run pytest -o addopts='' tests/test_agent_spec_config.py -q      → 18 passed
rv-2  uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q → 34 passed
rv-4  uv run pytest ... -k opencode_has_no_generate_profile               → 1 passed
rv-5  rg -n 'codebuddy' tests/ src/ + 两个生命周期测试文件                → 38 passed，无"未注册"用法残留
rv-6  派生 SUPPORTED_AGENT_SPECS                                          → [('codebuddy','codebuddy'),('qoder','qoder-cn'),('opencode','config/opencode'),('dsh' 不存在)]
rv-9  合并修正两条回归测试                                                → 2 passed；基线代码上同断言失败
rv-8  文档/原型枚举文本断言                                               → 通过
rv-10 CI=true just test all / just lint --full                            → 2394 passed / lint 通过（见 §5 过程披露）
```

`rv-3` 的真实 CLI 输出全文见 `rv-3-agent-doctor.txt`（同目录，本机文件）。关键片段：

```text
$ uv run iar agent list
codex ... claude ... kimi ... pi ...
codebuddy (bin: codebuddy, label: agent/codebuddy)
  run: delivery=argv_tail, protocol=claude-stream-json, writable
  generate: delivery=argv_tail, protocol=plain, read-only
qoder (bin: qodercn, label: agent/qoder)
  run: delivery=argv_tail, protocol=claude-stream-json, writable
  deliberate: delivery=stdin, protocol=plain, writable
opencode (bin: opencode, label: agent/opencode)
  run / deliberate / repl（无 generate）

$ uv run iar agent doctor opencode --all-profiles
[red]doctor failed:[/] agent 'opencode' has no 'generate' profile (declared: run, deliberate, repl).
```

## 3. rv-7：console 两层下拉（真实入口，人读呈递物）

**全局层：Settings → 生命周期 Agent 设置 → 全局矩阵下拉**

![console 全局层 agent 下拉含 codebuddy / qoder / opencode](./rv-7-console-agents-global.png)

**仓库层：Roadmap → 仓库行齿轮 → 仓库矩阵抽屉下拉**

![console 仓库层 agent 下拉含 codebuddy / qoder / opencode](./rv-7-console-agents-repo.png)

采集脚本判定的输出（全文见 `rv-7-console-agents.txt`，本机文件）：

```text
[rv7] global     下拉候选: 8 项 → PASS 含 codebuddy / qoder / opencode
[rv7] repository 下拉候选: 8 项 → PASS 含 codebuddy / qoder / opencode
[rv7] PASS 写回落盘：.iar.toml 新增 implementation = "codebuddy"
```

8 项 = 7 个已注册 agent + `auto`。写回后 fixture 的 `.iar.toml`：

```toml
[agent_runner]
[agent_runner.lifecycle_agents]
implementation = "codebuddy"
```

原有的 `[agent_runner]` 行未变，符合"只改对应文件、其余内容不变"。

### 采集环境（披露）

- 后端：**被测 worktree 的代码**，隔离实例（端口 8319，`HOME`/`IAR_CONFIG` 指向 `/tmp/iar-rv7` fixture；fixture 仓库 `git init` + commit 后注册）。
- 前端：**主仓库已构建的静态控制台产物** —— 本 PRD 未改任何前端代码，故沿用其构建物；这不构成"验的是 main"的问题，因为被测面是后端派生的 `agents` 数组。
- 只 mock 了 `/api/auth/me` 登录态（会话守卫是纯客户端的）；`/api/v1/agent-runner/lifecycle-agents` 与写回接口**都走真实后端**。
- 脚本：`scripts/rv7-console-agents.mjs`（同目录，本机文件）。断言同时覆盖"候选值集合"与"写回后文件内容"，不依赖截图肉眼读数。

## 4. 独立复核（两轮）

- **第一轮**：见 `P1-FEAT-20260920-225333-builtin-agent-coverage-codebuddy-qoder-opencode.verifier-report.md`。结论 PASS-with-caveats，命中 1 个 major（冻结树过不了 lint）+ 4 个 minor，均已整改。
- **第二轮**：整改后重跑（见 §5），`just lint --full` 与全量测试均通过。

## 5. 过程披露（不择优保留）

1. **lint 曾"假通过"**：首次 `just lint --full` 通过时，证据文件 `rv-3-agent-doctor.txt` 尚未加入；之后加入的文件带行尾空格，`trim trailing whitespace` 钩子改文件并判失败。独立 verifier 首轮即命中此点。清理行尾空格并重新全量测试刷新 test 标记后，`just lint --full` 真通过。
2. **测试的两次不同观察**：一次运行出现 2 条 `tests/test_cli_console.py::TestResolveConsolePort` 失败——本机 58323 端口被无关应用占用（`lsof` 显示 Clash Verge 与千问客户端各有一条到该端口的连接），并**在主仓库未改动的 HEAD 上复现同样 2 条失败**；该端口释放后重跑为 **2394 passed / 0 failed**（verifier 复核亦为 0 failed）。两种观察都记录在案。
3. **验证的边界**：三个新 agent 都没有跑过真实任务（会消耗模型额度）。`codebuddy` 与 `claude` 的同构性来自 `--help`；`qoder` 的流式协议复用来自"`-o` 接受 stream-json"的参数校验探针 + 安装包内 schema 与 claude 同形的读取，**未做真实流式运行**；`deliberate` 因此保持 `stdin` + `plain`。PRD §12 已把这一点显式披露。
4. **相邻文档修正**：本次一并修正了 `docs/guides/agent-runner.md` 里三处既有 stale 内容（工具路由标签表漏 `pi` 与新 agent / 「Planner 安全」段与门禁实现相反 / container auth 产出一段漏目录）。它们不改变任何行为。

## 6. 已知限制

- `read_only` 是**声明**，planner 门禁只读该字段，不校验 argv 是否真带沙箱开关；`codebuddy` / `qoder` 沿用 `claude` 的 `--dangerously-skip-permissions -p` 口径，不是沙箱级只读。PRD §12 已披露，并建议作为独立议题统一收紧。
- 容器路径未纳入本轮（runner 镜像未预装这三个 agent；容器里选中它们会在启动子进程时报可执行文件不存在）。已在 `docs/getting-started/installation.md` 披露。
- rv-7 使用主仓库预构建前端 + worktree 后端，已如实披露；它证明的是后端派生的下拉数据，不是前端源码改动（本 PRD 无前端源码改动）。
