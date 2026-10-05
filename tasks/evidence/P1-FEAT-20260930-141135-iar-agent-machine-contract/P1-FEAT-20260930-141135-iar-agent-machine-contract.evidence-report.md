# Evidence Report — P1-FEAT-20260930-141135-iar-agent-machine-contract

## 人审导航 / Human Review Navigation

本节复刻 PRD §9.1 的两行呈递物（本 PRD 全部证据为 CLI 文本捕获，无图片/录屏，因此无需内嵌媒体）。每项给出：呈递物路径（仓库相对路径）、打开命令、10 秒自检（精确到哪一行看什么值）。原始捕获在 `.iar/evidence/`（gitignore 排除，本地交付）；结构化清单 `.iar/evidence/evidence.json`。

| # | 你要看什么 | 呈递物（仓库相对路径）+ 打开命令 | 10 秒自检（位置 → 期望值） |
|---|---|---|---|
| 1 | rv-1：机器模式 stdout 是纯 JSON | `.iar/evidence/rv-1-issue-list-json.txt`，`open .iar/evidence/rv-1-issue-list-json.txt`；负控 `.iar/evidence/rv-1-negative-control.txt`；复跑 `bash .iar/evidence/scripts/rv1.sh` | 正向文件搜 `ok [stdout 是 JSON 数组] stdout satisfies jq -e type=="array"` 与 `两次独立进程的 stdout 逐字节一致`；负控文件搜 `stdout rejected by jq`（同命令去掉 `--json` 后首行是 `┏━━━` 表格边框） |
| 2 | rv-3：未找到/用法错误返回可区分退出码 | `.iar/evidence/rv-3-exit-codes.txt`，`open .iar/evidence/rv-3-exit-codes.txt`；负控 `.iar/evidence/rv-3-negative-control.txt`；复跑 `bash .iar/evidence/scripts/rv3.sh` | 搜 `ok [not_found 退出码] exit=3`；再搜一行 `not_found=3 usage=2 permission=4 conflict=5 dry_run=10 general=1`（六码两两不同）；负控文件搜 `baseline_exit_for_not_found=1（改造前）vs 3（改造后）` |

**已由执行侧与独立 verifier 交叉核验**（人默认可跳过）：rv-2/rv-4/rv-5/rv-6/rv-7 为 `reviewer: verifier` 项，agent 自验 + qoder 独立复核（verdict PASS / risk=green，`.iar/evidence/verifier-response.txt`）均已通过；人审清单见同目录 `human-review-checklist.md`（`just prd review tasks/archive/P1-FEAT-20260930-141135-iar-agent-machine-contract.md`）。

> 采集环境：worktree `.iar-worktrees/issue-194`，分支 `issue-194`（最终工作树含预 PR 复审的 Typer 层 envelope 补口，2026-10-05 20:32–20:54 全量复采 7/7 PASS）。
> 全量门禁：2995 passed, 1 skipped（139.12s）；`just lint --full` 全绿；`uv run mkdocs build --strict` 通过。

## 按验收条目的证据

### rv-1 · JSON 模式 stdout 合法且纯（`rv-1-issue-list-json.txt` / `rv-1-negative-control.txt`）

- 真实 `uv run iar issue list --json --repo-id keda`：exit=0，stdout 经 `jq -e 'type=="array"'`、`length>0`、`all(has("number"))` 三段断言；两个独立新进程 stdout 逐字节 `cmp` 相同。
- 负控：同一命令去掉 `--json`，exit=0 但 stdout 被 jq 拒绝，首行是 Rich 表格边框——证明「纯 JSON」是机器模式独有的、被测出来的属性。

### rv-2 · 数据与消息分离（`rv-2-agent-doctor-json.txt` / `rv-2-run-dry-run-json.txt` / `rv-2-negative-control.txt`）

- `iar agent doctor claude --json`：stdout 是被 jq 接受的条目数组、`.[0].argv | type=="array"`，且 stdout 中不存在 `INFO -`/`WARN`。
- `iar run --dry-run --json`：stdout 是含 `dry_run`/`repositories` 的计划对象且无日志行；同一进程的进度日志（logger 名含 `backend.core`）落在 stderr——双流对照，不是屏蔽 stderr 造成的假净。

### rv-3 · 语义退出码（`rv-3-exit-codes.txt` / `rv-3-negative-control.txt`）

- 六类真实进程退出码：not_found=3（`iar logs --repo-id does-not-exist --issue 1`）、usage_error=2（互斥旗标）、permission_denied=4（fake `gh` 未认证）、conflict=5（沙箱仓二次安装模板）、dry_run_ok=10（`iar run --dry-run --json`）、未分类=1（`blocked-continue --issue 999999`）；`sort -u | wc -l == 6` 证明码表真的接入。
- 每个失败类 stderr 都含可跑的下一步命令（`iar registry list`、`gh auth login -h github.com`、`iar workflow install preview --force`）。
- 负控：① 用法错误变体 exit=2≠3；② 改造前基线树同一命令 exit=1（3 是本次引入的语义）；③ 人类模式 dry-run exit=0≠10（10 只在机器模式生效，不污染既有脚本）。

### rv-4 · 结构化错误 envelope（`rv-4-error-envelope.txt` / `rv-4-negative-control.txt`）

- not_found / usage_error / permission_denied 三类 `--json` 失败的 stderr 均被 jq 断言为恰好五键 `["error","exit_code","message","retryable","suggestion"]`，`.error=="not_found" and .exit_code==3` 等同源等式成立，失败时 stdout 0 字节。
- `suggestion` 不是文案：not_found 的建议被原样反解执行，实测 exit=0 且 stdout 含真实仓库条目 `keda`。
- 预 PR 复审后补口：Typer/click 解析期失败（未知旗标、`--output` 枚举拒绝、缺必填参数）在机器模式下也落同一 envelope（`iar logs ... --json --bogus-flag` → exit 2、stderr 五字段、`suggestion: iar logs --help`），人类模式保持 click 原文逐字节不变。
- 负控：人类模式 stderr 不含 `exit_code` 字段且被 jq 拒绝；改造前基线树加 `--json` 只有 click 纯文本。

### rv-5 · 运行时自省（`rv-5-schema-introspection.txt` / `rv-5-negative-control.txt`）

- `iar schema --json`：顶层键 `["command_count","commands","exit_codes","help","name"]`，实测 46 条命令且 `command_count == (.commands|length)`；每个 option 条目恰为 9 个元数据字段（含 `type`/`required`/`enum`/`default`/`example`）；`exit_codes.values` 覆盖 7 类且 `.values["3"]=="not_found"`、`.values["10"]=="dry_run_ok"`。
- 同源验证：`iar logs --help` 长旗标集合与 schema `names[]` 逐条相同；`--kind` 取值域与 `enum` 一致；封闭例外清单（不配 `--json` 的命令恰为 `ask`/`deliberate`，其 `--output` 为目录型 `type=="text"`）。
- 负控：虚构旗标 `--fields` 让 oracle 变红（exit=4）；改造前基线树根本没有 `schema` 命令。

### rv-6 · 零回归（`rv-6-zero-regression.txt` / `rv-6-negative-control.txt`）

- 基线提交树 vs 当前树双侧快照：8 条只读命令人类输出 stdout+stderr 逐字节 `cmp` 相同、退出码相同；旧写法 `iar issue list --output json` 与基线逐字节相同；`--json` 与 `--output json` 在 issue list / registry list / agent list 上逐字节等价。
- 10 个 `--help` 页：词元多重集不丢失 + 基线词序为子序列（新增词元全部落在退出码说明/`--json`/`--output`/`schema` 四类允许文本内）。
- 判别力：反向比对实测丢失 171 个词元——正向「0 丢失」是真的测出来的。
- 唯一披露差异：`--output` 取值域由自由 TEXT 收紧为 `[table|json]`，`--output bogus` 从静默回落（exit 0）改为用法错误（exit 2）；PRD §14 已记录为唯一行为收紧。

### rv-7 · skill/docs 同步（`rv-7-skill-docs-sync.txt` / `rv-7-negative-control.txt`）

- `iar-operator/SKILL.md`：六条不变量文本（如 `--json` is an alias of `--output json`、stdout carries data only）+ 5 个 envelope 字段名 + 7 行退出码表全部命中；skill 表的 Name 列与 `iar schema --json` 的 `exit_codes.values` 一一对应（集合相等）；skill 示例旗标全部在运行时 schema 真实存在。
- `docs/guides/agent-runner.md` 与 `docs/api/references.md` 含「机读契约」小节并点名 `dry_run_ok`/`permission_denied`/`iar schema`；`mkdocs.yml` 未改动（Non-Goal 侧断言）。
- `tests/test_iar_operator_skill.py` → 9 passed；负控：改造前基线树的 skill/guide 不含这些文本（`ok 改造前 skill 不含：iar schema --json`），删行控制证明检查非恒真。

## 已知限制

1. **空结果流的机器语义**：`iar logs --json` 在 Issue 尚无输出（`NO_ATTEMPT`）时 exit 0、stdout 0 字节、说明走 stderr——「exit 0 + 空 stdout」应读作"暂无数据"而非管道故障（`jq -e .` 对空输入不报成功；§9.2"无条件通过"指成功且非空的结果）。需要机器可判别的空态哨兵应单独立小 PRD 评估（PRD §12）。
2. **`iar logs --json` 是 NDJSON 行流**（`{"line": …}`），非单文档 JSON；PRD 只承诺 list 型命令输出数组，行流可被 `jq` 逐行解析。
3. **issue list 内容随真实仓库状态变化**：rv-1 只锁形状（数组/非空/元素含 `number`），不锁条数。
4. 全量 pytest 的 testmon 档（`CI=true just test`）选中 169 例为增量路径；最终门禁以 `-o addopts=""` 全量 2995 passed 为准（记录在 `*.verification-plan.md`）。
