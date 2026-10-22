# 数据集样本导出

`python -m scripts.dataset_export` 把本机 KedaCode 的执行记录聚合成以 **Issue 为主键** 的 JSONL 样本，用于评测集、成功轨迹筛选和"失败→成功"偏好对的候选盘点。整个过程只读：SQLite 以 `mode=ro` 打开，git 只做查询，`gh` 仅在 `--with-gh` 时调用 `issue view` / `pr list`。

```bash
# 默认读 ~/.kedacode/console.db，样本写 stdout，统计摘要写 stderr
uv run python -m scripts.dataset_export --output samples.jsonl

# 只导出指定仓库，并用 gh 补 Issue 原文与关联 PR（每个 Issue 两次网络调用）
uv run python -m scripts.dataset_export --repo keda --with-gh --output samples.jsonl
```

默认排除 `rv*`、`process-runner-test`、`harness` 三类测试或演示仓库，可用 `--exclude-repo` 覆盖。

## 样本字段

| 字段 | 来源 | 说明 |
|---|---|---|
| `input.provenance` | 推断 | `verbatim_issue_body`（`--with-gh` 取到原文）/ `title_only`（仅 `run_records.issue_title`）/ `missing`。**拿不到原文不会伪装成原文**。 |
| `input.title` / `body` / `url` | `gh` 或 `run_records` | Issue 内容是导出时刻的快照，Issue 之后被编辑不会回溯。 |
| `code` | PR（`--with-gh`）或本地 `issue-N` 分支 | `source` 为 `pull_request` 时以 PR 的提交链为准（`base_sha` 是第一个 PR 提交的父提交，`final_sha` 是 PR head）；否则退回本地分支（与默认分支的 merge-base 到分支末端）。本地分支可能落后于 PR 后续推送，所以有 gh 时不用它。两者都没有时为 `null`。 |
| `pull_requests` | `gh pr list --head issue-N` | 原始 PR 列表（编号、状态、head、改动规模、提交）；未传 `--with-gh` 时为 `null`。 |
| `size` | PR 或 `git diff --numstat` | `files` / `additions` / `deletions`。 |
| `runs` / `attempts` / `invocations` | `console.db` | 运行结局、每次 attempt 的失败类型/preset/model、各阶段调用的 agent 与请求模型。 |
| `evidence` | `tasks/evidence/issue-N/evidence.json` | 条目数与带负控制（红→绿）的条目数；证据目录不入 git，只在本机存在。 |
| `labels` | 汇总 | 见下表。 |

样本不包含 `repo_path` 等本机绝对路径。轨迹原文（agent 日志）尚未导出，样本通过 `invocations` 只保留调用元数据。

## 标签

| 标签 | 含义 |
|---|---|
| `resolved` / `resolved_basis` | `resolved_basis=pr_merged` 时表示关联 PR 已合并（可信）；`run_completed` 只表示 runner 跑完，**不代表已合并**，仅在拿不到 PR 时作兜底。 |
| `task_kind` | 标题含"巡检/巡查"的自动巡检 Issue 为 `patrol`，其余为 `development`。 |
| `size_band` | `small`（≤5 文件且 ≤300 行）/ `medium`（≤30 文件且 ≤3000 行）/ `large`；规模未知为 `null`。 |
| `merge_commit_count` | base..final 间的 merge commit 数；非 0 说明混入了别的分支的改动；提交对象不在本地时为 `null`（未核实）。 |
| `has_commits` / `has_evidence` | 有提交链 / 有 `evidence.json`。 |
| `has_preference_pair` | 存在先失败、后成功的 attempt。 |
| `eval_ready` | 下一节的组合条件。 |

## 摘要中的 `eval_ready`

同时满足以下条件的样本：PR 已合并（`resolved_basis=pr_merged`）、有提交、输入不是 `missing`、`task_kind=development`、`size_band` 不是 `large`、`merge_commit_count` 为 0（`null` 即未核实，不算通过，会计入摘要里的 `unverified_history`）。巡检类样本仍会出现在输出里（便于统计），只是不计入 `eval_ready`。**不加 `--with-gh` 时 `eval_ready` 恒为 0**，因为没有 PR 合并状态就无法确认任务真的解决了。评测集还需要每个样本有 `check_recipe`，这一步目前仍需人工补。

## 去重

同一仓库可能以多个 `repo_id` 注册（如 `keda` 与 `keda-main` 指向同一路径）。同一仓库路径下相同的 Issue 编号只导出一份：保留运行记录最多的 `repo_id` 作为样本主键，其余记在 `alias_repo_ids`，运行与 attempt 记录会合并。

## 已知限制

- 没有 `--with-gh` 时提交关联只认本地 `issue-N` 分支，可能落后于 PR 或已被删除。
- PR 以 `issue-N` 为 head 分支名查找；不按此命名的 PR 找不到。
- 一个 Issue 多个 PR 时只取已合并的最新一个。
- 阈值（规模分档、巡检关键词）是经验值，写在 `export_samples.py` 顶部常量里。
- 导出不做脱敏；对外发布前需单独审查 `input.body`、`error_summary` 中是否含内部信息。
