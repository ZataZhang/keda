PASS

验证范围：本机独立复核对 PRD §9 未勾的 29 项执行侧条目逐一对照证据。所有判据以 PRD §7.6 oracle 为准。

## 核对结论摘要

- **代码树身份**：worktree 在 `docs/archive-confirmation-separation` @ `c2fcc4b5`，与 main 相同；工作区仅两份证据 `.md` 为脏（归档提交要动的 record 路径，预期内）。按证据报告「绑定最终树」的命令用临时 index 复算 record-excluded tree，结果恰为 `81b833554c9c8be9481f21fa7d80a88a2c204468`；`src/`/`tests/`/`docs/`/`hooks/` 四棵子树 id 与报告表格逐一相等。证据未过期。
- **rv-1 / rv-5（人审 oracle，R2/R3）**：归档轮 `raw/installed-v5/` 日志首行均写明 `IAR_PRD_SKILL_PATH unset`、HEAD `c2fcc4b5`、已安装 skill `Machine-Contract-Version: 5`。rv-1 内嵌记录与报告逐字一致：`R  tasks/pending/example.md -> tasks/archive/example.md`、pending 目录 `(empty)`、归档后横幅行仍是 🧍、`### Human-Confirmed` 下仍是 `- [ ]`。rv-5 两轮记录一致：第一轮 `outcomes=['skipped_human_review']`、`process calls: []`、github calls 无 `merge_pull_request`；第二轮人回答后 `outcomes=['merged']` 且 process calls 含 `git rebase origin/main`。测试源码（见负控日志回显）证实 rv-1 走真实 `SubprocessRunner` + 真实 git fixture，rv-5 走 `process_merge_queue` 公开入口，PRD 为真实文件，未打桩解析。
- **负控两层均成立**：实现前红跑日志为 `2 failed`，失败形态恰为 PRD 写明的 `expected_fail`（rv-1 归档文件不存在、rv-5 `['merged'] == ['skipped_human_review']` 断言失败）；判别矩阵 `27 failed, 123 passed`，导入探针证实跑的是未修改源码。
- **rv-2/3/4/6/7、FR-6**：归档轮日志 pass 数与报告一致（1/9/3/29/5/11），exit 0。
- **Drift Guard 10 行**（`raw/installed-v5/drift-guard.log`）：逐行核为 ✓；我另行 grep 复核了契约版本 `(3, 4, 5)`、`_validate_acceptance_banner`、`resolve_prd_worktree_path` 复用、marker 与小节标题逐字未变、旧语义短语在 `src/backend`+`docs` 无残留、`hooks/check_prd_archive_pre_push.py` 不存在。
- **低风险门禁**：全量 pytest `2786 passed, 1 skipped`（`full-suite.log` 尾部与首行树 id 核对）；架构守卫/ruff/mkdocs strict/依赖面在 `static-gates.log` 通过；`just test` 与 `just lint --repo` exit 0。`git diff --name-only ed30c170 c2fcc4b5` 与 Change Impact Tree 吻合，无 `uv.lock`/`alembic/`/`frontend-*`。
- **§8 Notes (a)–(d) 顺序**：`git log` 时间戳（bd735e85 04:29 UTC → ed30c170 08:13 UTC → c2fcc4b5 08:14:46 UTC）与报告合并顺序表一致；本机已安装 v5（`~/.iar/skills/prd/SKILL.md:563`）；清单钩子 `HUMAN_CONFIRMED_GROUP_PREFIX` 在位。
- **独立外部核对**：main 上 `c2fcc4b5` 的 push CI（run `37188292685`）状态 Success，与报告声明一致。
- **27 项可勾性**：上列证据逐条覆盖「尚未完成」表格中 27 项各自的证据引用；另 2 项（§13 Final Reconciliation + 横幅、完成回复带 §9.1 呈递表）由归档提交本身完成，当前无任何东西阻碍。3 项 Human-Confirmed 与 2 项 `[~]` runner-owned gate 保持开放属设计内。
- **安全扫描**：对全部证据日志与两份报告做凭据/密钥模式扫描（token、API key、私钥头、云厂商 key 模式），无命中。日志中仅有本机路径与 tmp 路径，不构成泄露。

## NON-BLOCKING

- [归档轮 `just test`] testmon 增量档当轮 pytest 实为 `no tests ran`，实质全量覆盖由关掉了 testmon 的 `full-suite.log`（2786 passed）提供。证据报告「提交门禁」一节已如实披露该口径，命令可复现，不影响任何被勾条目的真实性。
- [首轮 raw 日志] 首轮日志经 `IAR_PRD_SKILL_PATH` 指向模板 v5（当时本机装 v4）收集；归档轮已用本机安装的 v5 在同一棵树 `c2fcc4b5` 上全部重跑，结果逐条相同，且每份归档轮日志首行自证 `IAR_PRD_SKILL_PATH unset`。归档轮证据单独即可支撑结论，首轮日志仅作旁证。
- [人审导航 #4] 归档轮 PR 的 CI 链接在取证时点仍标 ⏳（报告中已披露）；main 上 `c2fcc4b5` 的 push CI 全绿已独立核实，且 Validation 组两项 `[~]`（runner-owned gate）本来就不由本轮勾选。
