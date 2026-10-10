# Verification Plan — 夜间任务批次聚合为单一总 PR

## Scope

验证限定在本地 CLI、临时 Git 仓库和纯字符串 PR body contract。此计划不连接 GitHub、不读取仓库 Issue / PR、不发布或更新 PR，也不使用 fake GitHub、录制响应或 mock 状态作为证据。

| ID | Oracle | Command | Expected result |
|---|---|---|---|
| rv-1 | `kc run` 聚合参数默认关闭；有效组合可解析，目标 / bypass / 不足配额在执行器前拒绝；手动命令 help 暴露重复 `--issue`。 | `bash tasks/evidence/P1-FEAT-20261009-161921-nightly-batch-aggregate-pr/scripts/run-rv-1.sh` | Parser / schema assertions pass；两个真实 CLI help 命令 exit 0；不启动队列或外部客户端。原始终端输出在本地忽略文件 `rv-1-cli.txt`。 |
| rv-2 | 显式本地 source SHA 按序合并到真实临时 worktree；冲突、不匹配已验证 PR head 的 batch ref 更新和未拥有的 batch branch 均拒绝；总 PR 创建失败时只按匹配 SHA lease 清理本次未发布分支；不改 base / source refs。 | `bash tasks/evidence/P1-FEAT-20261009-161921-nightly-batch-aggregate-pr/scripts/run-rv-2.sh` | 自动临时仓库与 bare remote 的 Git tree / refs assertions pass。原始输出在本地忽略文件 `rv-2-git.txt`。 |
| rv-3 | 聚合 v2 接受精确、稳定排序且去重后的 PRD 集；漏项、重复、额外项、来源不匹配及缺少接受声明被拒绝；普通 v1 contract 保持兼容。 | `bash tasks/evidence/P1-FEAT-20261009-161921-nightly-batch-aggregate-pr/scripts/run-rv-3.sh` | 本地 v1/v2 contract tests pass；不创建或发布 PR。原始输出在本地忽略文件 `rv-3-body-contract.txt`。 |
| rv-4 | GitHub 创建 / checks / close / comments / branch retention / merge。 | **Not run by request.** | 不收集或伪造通过证据；本地记录 `rv-4-external-github-unverified.txt` 仅说明验证边界。 |

## Additional gate

After the final implementation change, `UV_CACHE_DIR=/private/tmp/uv-cache-issue-258 uv run pytest --no-testmon -q tests/test_agent_runner_batch_aggregate.py` ran the complete Git oracle file and reported **19 passed**. The default `just test` run also ran those 19 tests but failed 10 unrelated `kc config migrate` cases because the sandbox denies process enumeration; four tests were deselected. A scoped `just test` invocation reported 19 deselected under pytest-testmon and is not counted as evidence. `tests/test_agent_runner_cli.py -k aggregate` reported **4 passed**, the PR body contract subset reported **18 passed**, and focused direct-PR / GitHub-client / packaged-skill compatibility tests reported **110 passed**. `uv run mkdocs build --strict` exited 0 with existing unlinked-page and reciprocal-anchor informational messages.

An earlier broad `just test` run was interrupted after 581 passed and 1 skipped; it is not acceptance evidence. The first batch-fixture run exposed missing `git add` calls in the new test fixture. The fixture was corrected, then the complete targeted file passed.
