# Verification Plan — P1-FEAT-20260930-141135-iar-agent-machine-contract

> 交付基线：worktree `.iar-worktrees/issue-194`，分支 `issue-194`（rebase 到 `zata/main` cc5dd64 之上，交付提交为 runner 随后创建的 `4eb36ab` 后继提交）。
> 全量门禁（最终工作树，2026-10-05 20:55–21:01 复测）：`uv run pytest tests/ -q -o addopts="" --ignore=tests/playwright-e2e` → **2995 passed, 1 skipped**（唯一 skip 为既有 alembic 迁移守卫：本仓 `alembic/versions` 为空，环境性跳过，与本 PRD 无关）；`just lint --full` 全部 hook Passed；`uv run mkdocs build --strict` 通过（仅两条与本改动无关的既有锚点 INFO）。
> 独立 verifier（qoder）verdict：**PASS / risk=green**（复核基线 `afd1a8d`＝rebase 后 `98739a1`），原始回执 `.iar/evidence/verifier-response.txt`，结构化报告见同目录 `*.verifier-report.md`。
> 注意：全量 pytest 不得携带 `IAR_SKIP_GH_AUTH_CHECK=1`（该变量按设计跳过 gh 认证预检，会使 `tests/test_gh_auth_check.py::test_ensure_gh_auth_or_prompt_exits_on_failure` 变红）；rv 脚本按需自行设置该变量以跳过对 GitHub 网络的依赖，与被测 CLI 链路无关。

## rv 对应验证命令

| rv-id | 行为 | 验证方式（真实入口） | 证据文件 |
|---|---|---|---|
| rv-1 | 目标命令的 JSON 模式 stdout 是合法且纯的机器输出 | 真实 CLI 入口 `uv run iar issue list --json --repo-id keda` 经 `jq -e 'type=="array"'` 断言；两次独立进程 stdout `cmp` 逐字节一致；负控 = 去掉 `--json` 后同一 stdout 必须被 jq 拒绝 | `rv-1-issue-list-json.txt` / `rv-1-negative-control.txt` |
| rv-2 | 数据与消息分离——stderr 承载警告/错误，stdout 保持纯净 | `iar agent doctor claude --json`（stdout 无 `INFO -`、`.[0].argv` 为数组）+ `iar run --dry-run --json`（进度日志落在 stderr，logger 名 `backend.core`）双流对照 | `rv-2-agent-doctor-json.txt` / `rv-2-run-dry-run-json.txt` / `rv-2-negative-control.txt` |
| rv-3 | 未找到类失败返回语义退出码 3 | 六类真实进程退出码核对（not_found=3 / usage=2 / permission=4 / conflict=5 / dry_run=10 / 未分类=1），每类 stderr 断言可跑建议；三重负控（用法错误变体≠3、改造前基线树=1、人类模式 dry-run=0≠10） | `rv-3-exit-codes.txt` / `rv-3-negative-control.txt` |
| rv-4 | JSON 模式下的失败是结构化错误 envelope | not_found / usage_error / permission_denied 三类 `--json` 失败 stderr 经 jq 断言恰好五键 `["error","exit_code","message","retryable","suggestion"]`、失败时 stdout 0 字节；`suggestion` 反解出来真实执行成功（exit=0） | `rv-4-error-envelope.txt` / `rv-4-negative-control.txt` |
| rv-5 | `iar schema` 从真实命令树提供运行时自省 | `iar schema --json` 派生 46 条命令；`issue list --output` 的 enum 恰为 `["table","json"]`；option 恰为 9 个元数据字段；`exit_codes.values` 覆盖 7 类；`--help` 长旗标集合与 schema `names[]` 同源比对；负控 = 虚构旗标 `--fields` 让 oracle 变红 | `rv-5-schema-introspection.txt` / `rv-5-negative-control.txt` |
| rv-6 | 默认人类输出与旧写法零回归 | 改造前基线提交树 vs 当前树双侧快照：8 条只读命令 stdout+stderr 逐字节 `cmp` 且退出码相同；10 个 `--help` 页词元多重集+词序子序列比对（反向比对实测丢 171 词，证判别力）；旧写法 `--output json` 与基线逐字节一致；`--json` 与 `--output json` 等价 | `rv-6-zero-regression.txt` / `rv-6-negative-control.txt` |
| rv-7 | 新契约同步进 operator skill | `iar-operator/SKILL.md` 六条不变量 + 5 个 envelope 字段名 + 7 行退出码表命中；skill 退出码表与 `iar schema --json` 的 `exit_codes.values` 集合相等；skill 示例旗标全部在运行时 schema 真实存在；`docs/guides/agent-runner.md`、`docs/api/references.md` 含机读契约小节；`mkdocs.yml` 未改动；`tests/test_iar_operator_skill.py` → 9 passed | `rv-7-skill-docs-sync.txt` / `rv-7-negative-control.txt` |

每个 rv 的完整可复跑命令见 `.iar/evidence/evidence.json` 的 `command` 字段（脚本路径 `.iar/evidence/scripts/rv1.sh … rv7.sh` + 共用断言库 `_rv_lib.sh`，全部在 gitignore 的 `.iar/` 下，不进入代码 diff）。2026-10-05 20:32–20:54 已在最终工作树（含预 PR 复审的 Typer 层 envelope 补口）全量复跑 7/7 PASS。

## 架构验收命令

- `rg -n "print_json|json\.dumps|json_output" src/backend/api` → 仅命中 `cli_output.py`（单一序列化出口，无旁路）。
- `rg -n "return 1" src/backend/api/cli_parsed_commands` → 现存 11 处逐点核对均为「未分类失败」保持 1（`agent.py:251/253/257`、`labels_issue.py:220/279/315`、`config_migrate.py:116`、`runner.py:326/371`、`worktree.py:105`、`init_workflow_takeover.py:80`）；not_found/conflict/usage/permission 点已改抛 `CliError(code=ExitCode.*)`。
- `uv run iar schema --json | jq -e '.commands | length > 0'` → 真实入口通过（46 条命令）。

## mock 边界

- rv 证据全部走真实 `uv run iar` 进程（Typer 入口 `backend.api.cli_typer_app.main`），断言只认 stdout/stderr/退出码；不调用 `render_*_json`、不 mock emit、不把 handler 返回值当 stdout。
- 唯一替换：`rv-3`/`rv-4` 的 permission 场景用 PATH 上的 fake `gh` 报告未认证（替换外部认证探测这一不确定性来源），被验证的 CLI 输出/退出码链路仍是真实实现；`IAR_SKIP_GH_AUTH_CHECK=1` 仅用于跳过对 GitHub 网络的预检，读取仍是真实注册表。
- `rv-3` 的 conflict 场景在 `.iar/tmp/` 下的临时 git 沙箱仓中执行（每次运行前重建），不触碰真实仓库。
- 单测层（`FakeProcessRunner` 等）只用于 `tests/` 内单元测试，不作为 rv 证据。
