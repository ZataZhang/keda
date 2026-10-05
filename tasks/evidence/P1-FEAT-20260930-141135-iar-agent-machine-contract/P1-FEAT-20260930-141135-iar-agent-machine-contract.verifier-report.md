# Verifier Report — P1-FEAT-20260930-141135-iar-agent-machine-contract

VERDICT: PASS（risk=green）

- 验证人：独立 verifier（qoder；只读复核 + 只读测试重跑，未改动任何源码 / 测试 / 证据 / PRD）
- 复核对象：worktree `.iar-worktrees/issue-194`，交付分支 `issue-194`
- 复核基线：builder sha `afd1a8da4f9d42b29f01fa5bfcbbd6d8906ed7ea`（rebase 前）＝ rebase 后 `98739a1`（同一变更集，rebase 到 `zata/main` cc5dd64 之上）；verifier 原始回执见 `.iar/evidence/verifier-response.txt`
- 复核日期：2026-10-05

## Frozen-State Check

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| builder sha | `afd1a8da…`（交付树） | verifier 在交付树上独立复跑 | ✅ |
| rv 脚本作弊审计 | 无假绿路径 | rv1…rv7 脚本通读：仅 permission 场景用 PATH 上的 fake `gh` 替代外部认证探测，被验证的 CLI 输出/退出码链路全是真实实现 | ✅ |
| 证据形态 | 14 个证据文件全为文本 | 全部 `.txt`，无图片/音视频 | ✅ |
| rv 复跑 | 7/7 PASS | verifier 自己重跑 rv1…rv7 全部 PASSED，且每个 oracle grep 命中再生成的证据 | ✅ |

## Findings

| id | severity | finding | evidence | required action |
|---|---|---|---|---|
| V-01 | LOW | `iar logs --repo-id keda --issue 999999 --json`（Issue 尚无输出）→ exit 0、stdout 0 字节、stderr 为人类文本提示；`jq -e .` 对空输入不成立，"stdout 可被 jq -e . 无条件解析"在空结果边界字面上过度声称 | verifier 实测 + `rv-3`/`rv-4` 未覆盖该空态 | 已处置：PRD §9.2 措辞收紧为"成功且非空的结果可被 jq -e . 无条件解析"，§12 新增「空结果流的机器语义」风险条目（exit 0 + 空 stdout 读作"暂无数据"） |
| V-02 | LOW | `iar logs --json` 有内容时是 NDJSON 行流（`{"line": …}`），非单文档 JSON | verifier 实测 | 无需处置：PRD 只承诺 list 型命令输出数组，NDJSON 行流 jq 可逐行解析；已在 §12 一并披露 |
| V-03 | LOW | rv6 的 `worktree path --repo-id keda` 快照对是退化对（基线树与当前树都把 `--repo-id` 判为用法错误，exit 2） | verifier 审阅 rv6 脚本 | 无需处置：verifier 已自行补做非退化验证——`git archive` 解出基线树 + PYTHONPATH 跑 `worktree path --branch issue-194`，stdout 与 stderr 均与当前树逐字节一致 |

无 HIGH / MEDIUM 发现。

## Per-rv Judgment

| rv-id | 判定 | 依据（verifier 独立复跑 + 代码交叉核对） |
|---|---|---|
| rv-1 | PASS | `iar issue list --json --repo-id keda` stdout 满足 `jq -e 'type=="array"'`；WARN 只在 stderr；两个独立进程 stdout 逐字节相同（29,691 B）；旧写法 `--output json` 等价；不带旗标的人类模式被 jq 拒绝（rc=5）且表格完好 |
| rv-2 | PASS | `iar agent doctor claude --json` stdout 纯 JSON、`.[0].argv` 为数组、stdout 无 `INFO -`；`iar run --dry-run --json` 进度日志落在 stderr（含 `backend.core` logger 名）——双流对照，非屏蔽 stderr 的假净 |
| rv-3 | PASS | 六类退出码各经真实进程核对：`not_found=3 / usage_error=2 / permission_denied=4 / conflict=5 / dry_run_ok=10 / 未分类=1`，两两不同；每个失败类 stderr 都含可跑的下一条命令（`iar registry list`、`gh auth login -h github.com`、`iar workflow install preview --force`）；基线提交树同一命令 exit=1（证明 3 是本次引入的语义）；人类模式 dry-run 仍 0（10 不污染既有脚本） |
| rv-4 | PASS | not_found / usage_error / permission_denied 三类在 `--json` 下 stderr 均为恰好五键 envelope（`keys|sort == ["error","exit_code","message","retryable","suggestion"]`），失败时 stdout 0 字节；not_found 的 `suggestion`（`iar registry list`）被原样反解执行成功（exit=0、stdout 含 `keda`）；负控：人类模式 stderr 不含 `exit_code` 字段、被 jq 拒绝 |
| rv-5 | PASS | `iar schema --json` 实跑 46 条命令，`command_count == (.commands|length)`；`issue list --output` 的 enum 恰为 `["table","json"]`、default `"table"`；option 条目恰为 9 个元数据字段；`exit_codes.values` 覆盖 `{0,1,2,3,4,5,10}` 且名称与信封 `error` 取值同源；`iar logs --help` 长旗标集合与 schema `names[]` 逐条相同（同源派生）；`--fields` 虚构旗标让 oracle 变红（exit=4）证明非恒真 |
| rv-6 | PASS | 基线树 vs 当前树双侧快照：8 条只读命令人类输出 stdout+stderr 逐字节 `cmp` 相同、退出码相同；旧写法 `--output json` 与基线逐字节相同；10 个 `--help` 页词元多重集无丢失且词序保持（反向比对丢失 171 词，证明检查有判别力）；`--json` 与 `--output json` 输出逐字节等价。唯一披露差异：`--output` 取值域由自由 TEXT 收为 `[table|json]`，`--output bogus` 从静默回落（exit 0）改为用法错误（exit 2）——PRD 已记录为唯一行为收紧 |
| rv-7 | PASS | `iar-operator` SKILL.md 六条不变量文本 + 5 个 envelope 字段名 + 7 行退出码表全部命中；skill 退出码表 Name 列与 `iar schema --json` 的 `exit_codes.values` 集合相等；skill 示例旗标全部在运行时 schema 中真实存在；`docs/guides/agent-runner.md` 与 `docs/api/references.md` 含机读契约小节并点名 `dry_run_ok`/`permission_denied`/`iar schema`；`mkdocs.yml` 未改动；`tests/test_iar_operator_skill.py` 复跑通过 |

## Verifier 原始结论摘录

> Intent matches behavior: machine output is explicit and pure, failure categories are distinguishable via exit codes with structured envelopes and runnable suggestions, schema is runtime-derived and consistent with the real command tree, and human mode is byte-identical to baseline. The empty-log edge is a minor documented-behavior nit, not an oracle violation.
>
> —— `.iar/evidence/verifier-response.txt`（`<!-- iar:verifier-verdict risk=green -->`）

## 后续轮次说明

本报告为独立 verifier 的原始复核（builder sha `afd1a8d` / rebase 后 `98739a1`）。其后交付树又经历两轮 executor 修补（解析期失败收口 FR-4 envelope 的 `7dec2b8`、预 PR 复审的 Typer 层 envelope 补口与记录对账——见 PRD §14 Change Log），修补后 executor 已按同一 rv 门禁全量复跑 7/7 PASS（2026-10-05 20:32–20:54，含 `cli_typer_app.py` 未提交改动的最终工作树），全量 pytest 2995 passed / 1 skipped、`just lint --full` 全绿（除 test-flag 刷新属 `just test` 职责外）、`mkdocs build --strict` 通过。修补未触碰 verifier 已判定的任何行为面之外的范围。
