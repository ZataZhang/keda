# 数据集样本导出

`python -m scripts.dataset_export` 把本机 KedaCode 的执行记录聚合成以 **Issue 为主键** 的 JSONL 样本，用于评测集、成功轨迹筛选和"失败→成功"偏好对的候选盘点。整个过程只读：SQLite 以 `mode=ro` 打开，git 只做查询，`gh` 仅在 `--with-gh` 时调用 `issue view` / `pr list`。

```bash
# 默认读 ~/.kedacode/console.db，样本写 stdout，统计摘要写 stderr
uv run python -m scripts.dataset_export --output samples.jsonl

# 只导出指定仓库，并用 gh 补 Issue 原文与关联 PR（每个 Issue 两次网络调用）
uv run python -m scripts.dataset_export --repo keda --with-gh --output samples.jsonl
```

默认排除 `rv*`、`process-runner-test`、`harness` 三类测试或演示仓库，可用 `--exclude-repo` 覆盖。

## 来源：不只是 keda 跑过的任务

样本带 `origin` 字段，说明它是怎么被发现的。数据集不应该只覆盖 keda 创建或执行的任务，所以支持多种来源，每种能还原的信息不同：

| `origin` | 怎么发现 | 任务输入 | 有尝试与失败记录 |
|---|---|---|---|
| `keda_run` | `console.db` 里的运行记录 | 认领时快照 > 事后抓取的 Issue 原文 > 仅标题 | 有（可构成偏好对） |
| `github_pr` | `--github-pr-repo <本地仓库>`：该仓库已合并、且不是 keda 导出过的 PR | 关联 Issue 的正文（`verbatim_issue_body`）；没有关联 Issue 时只有 PR 描述（`pr_description`） | 无 |
| `agent_session` | `--agent-sessions-repo <本地仓库>`（Claude Code）或 `--codex-sessions-repo <本地仓库>`（codex）：该仓库的真人会话 | 会话里用户**逐字**的首句，脱敏后导出（`session_first_prompt`） | 无（不导出完整轨迹） |

```bash
# 额外导出一个仓库里与 keda 无关的已合并 PR（需要 gh 已登录；可重复；不依赖 console.db）
uv run python -m scripts.dataset_export --github-pr-repo ~/code/some-project --pr-limit 100 --output samples.jsonl
```

- 跳过机器人账号（如 Dependabot）的 PR，以及 head 分支为 `issue-N` 且该 Issue 已作为 `keda_run` 样本导出的 PR，避免重复。
- **`pr_description` 不能当评测题**：PR 描述写的是"改了什么"，不是任务本身，等于泄露答案；这类样本只作重建记录保留，永远不计入 `eval_ready`。要让非 keda 的任务成为合格评测题，需要它有关联的 Issue（PR 描述里写 `Closes #N`）。
- 非 keda 来源没有 attempt，所以 `has_preference_pair` 恒为 `false`，不能产出偏好对。
- 为避开 GitHub GraphQL 的节点上限，先列出轻量信息筛选，再逐个取剩下 PR 的详情；每个 PR 至少一次网络调用（有关联 Issue 再加一次），80 个 PR 大约一分多钟。
- 尚未支持：没有 PR 也没有会话记录的普通提交。

### 会话记录（`agent_session`）

交互式会话的 jsonl 里有当时逐字的用户首句、会话期间做过的提交；Claude Code 的会话还带与 PR 的链接（`pr-link`）。支持两种 Agent，`session.agent` 标明是 `claude_code` 还是 `codex`。这是 keda 之外最接近"原始输入"的数据，也最敏感，所以**必须显式开启**：

```bash
# Claude Code 与 codex 可同时开启，仓库参数都可重复
uv run python -m scripts.dataset_export \
  --agent-sessions-repo ~/code/some-project \
  --codex-sessions-repo ~/code/some-project \
  --output samples.jsonl
```

- **Claude Code**：只读该仓库对应的会话目录（`~/.claude/projects/<仓库绝对路径，非字母数字换成 ->`，可用 `--sessions-root` 改根目录）。
- **codex**：会话按日期分区（`~/.codex/sessions/年/月/日/*.jsonl`，可用 `--codex-sessions-root` 改根目录），不按仓库分，所以先只读每个文件第一行的元数据，按会话的 `cwd` 是否等于该仓库或其子目录来归属，再完整解析匹配的文件。
- **只取真人会话**：Claude Code 的 `entrypoint=sdk-cli`、codex 的 `codex exec`（`originator=codex_exec`）以及 codex 子代理线程，都是程序化会话（keda runner、verifier、探针），一律排除——它们已经是 `keda_run` 样本。斜杠命令注入、子代理、元消息、codex 注入的 `# AGENTS.md instructions` 与环境上下文也不算用户首句。
- **只有能对上本地 git 的会话才构成样本**：从 `git commit` 的工具输出（codex 是 `exec` 工具调用的输出）里取提交哈希，再对照本地仓库；改写历史（rebase、amend）后哈希对不上的提交直接丢弃，不猜。没有任何可对照提交的会话（纯讨论、纯排查）不导出。
- `code.base_sha` 是第一个提交的父提交，`final_sha` 是最后一个提交；`resolved` 表示 `final_sha` 已在默认分支上（`resolved_basis=on_default_branch`）。
- **输入永远偏弱，永远不计入 `eval_ready`**：首句往往是对话式的（"你看一下这个"），不是任务规格。样本只是原始输入的事实记录，是否能当题由后续人工与更强的 Agent 判断。
- **不导出完整轨迹，也不带本机路径**；`session` 里只有会话 id、入口、起止时间、真人发言轮数、使用的模型、分支和 `linked_prs`。首句超过 2 万字符会截断并标 `truncated`。
- 与其他来源互相链接（仅 Claude Code，codex 会话没有 `pr-link`）：会话里的 `pr-link` 若能对上 `github_pr` 样本或带 `--with-gh` 的 `keda_run` 样本，会话样本的 `session.linked_sample_ids` 列出对应样本，对方样本的 `linked_session_ids` 列出会话 id。这样可以把"当时说了什么"和"最后做成了什么"放在一起看。

#### 脱敏的边界

首句经基于模式的脱敏：私钥块、`sk-`/`ghp_`/`AKIA`/`xox` 等常见令牌、`Bearer`、JWT、`api_key=…`/`password: …` 这类赋值、邮箱，以及 `/Users/<name>`、`/home/<name>` 家目录路径（替换成 `~`）。`input.redaction_count` 记录命中次数，`input.redaction_reviewed` 恒为 `false`。

**这只是模式匹配，不保证抓全**（比如自然语言里说出的口令、内部系统名、客户名都抓不到）。发布或分享数据集之前必须人工抽查 `input.body`。

## 样本字段

| 字段 | 来源 | 说明 |
|---|---|---|
| `origin` | 来源 | `keda_run` / `github_pr` / `agent_session`，见上一节。 |
| `input.provenance` | 推断 | `snapshot_at_claim`（有认领时快照，见[认领时的任务输入快照](agent-runner.md)）/ `pr_description`（仅 `github_pr`，只有 PR 描述，输入偏弱）/ `session_first_prompt`（仅 `agent_session`，真人会话首句，输入偏弱）/ `verbatim_issue_body`（`--with-gh` 事后取到原文）/ `title_only`（仅 `run_records.issue_title`）/ `missing`。**拿不到原文不会伪装成原文**；事后取到的原文只是"导出时刻"的样子，不一定是 Agent 当初看到的。 |
| `input.title` / `body` / `url` | 最早快照，其次 `gh`，其次 `run_records` | 有快照时取**最早一条**（Agent 认领时读到的）；没有快照的旧 Issue 行为不变。 |
| `input.edited_since_claim` | 快照与 GitHub 现状比对 | 认领后正文是否被改过：`true` / `false`；没有快照无从判断时为 `null`。最新已知正文优先取 GitHub 现状（`--with-gh`），否则取最后一条快照。 |
| `code` | PR（`--with-gh`）或本地 `issue-N` 分支 | `source` 为 `pull_request` 时以 PR 的提交链为准（`final_sha` 是 PR head；`base_sha` 默认是第一个 PR 提交的父提交，**有认领时快照则改用快照里记录的分叉点**，并在 `base_sha_source` 标明 `snapshot` / `inferred`）；否则退回本地分支（与默认分支的 merge-base 到分支末端）。本地分支可能落后于 PR 后续推送，所以有 gh 时不用它。两者都没有时为 `null`。 |
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
| `task_kind` | 标题含"巡检/巡查"的自动巡检为 `patrol`；Conventional Commits 的 `docs` / `chore` 类标题（归档 PRD、同步模板等）为 `maintenance`；其余为 `development`。只有 `development` 才可能 `eval_ready`。 |
| `size_band` | `small`（≤5 文件且 ≤300 行）/ `medium`（≤30 文件且 ≤3000 行）/ `large`；规模未知为 `null`。 |
| `merge_commit_count` | base..final 间的 merge commit 数；非 0 说明混入了别的分支的改动；提交对象不在本地时为 `null`（未核实）。 |
| `has_commits` / `has_evidence` | 有提交链 / 有 `evidence.json`。 |
| `has_preference_pair` | 存在先失败、后成功的 attempt。 |
| `eval_ready` | 下一节的组合条件。 |

## 摘要中的 `eval_ready`

同时满足以下条件的样本：PR 已合并（`resolved_basis=pr_merged`）、有提交、输入不是 `missing`，也不是仅 `pr_description` / `session_first_prompt`、`task_kind=development`、`size_band` 不是 `large`、`merge_commit_count` 为 0（`null` 即未核实，不算通过，会计入摘要里的 `unverified_history`）。巡检类样本仍会出现在输出里（便于统计），只是不计入 `eval_ready`。**不加 `--with-gh` 时 `eval_ready` 恒为 0**，因为没有 PR 合并状态就无法确认任务真的解决了。评测集还需要每个样本有 `check_recipe`，这一步目前仍需人工补。

## 去重

同一仓库可能以多个 `repo_id` 注册（如 `keda` 与 `keda-main` 指向同一路径）。同一仓库路径下相同的 Issue 编号只导出一份：保留运行记录最多的 `repo_id` 作为样本主键，其余记在 `alias_repo_ids`，运行与 attempt 记录会合并。

## 提示，非结论

导出器里的 `eval_ready`、`size_band`、`task_kind` 等是**启发式提示**，用来做廉价的初筛，不是对样本好坏的结论。KedaCode 只负责如实记录事实；样本是否可用、验证命令是否正确、描述是否清楚，应由后续的人工与更强的 Agent 在整理层判断，并能随时基于原始记录重做。

## 已知限制

- 没有 `--with-gh` 时提交关联只认本地 `issue-N` 分支，可能落后于 PR 或已被删除。
- PR 以 `issue-N` 为 head 分支名查找；不按此命名的 PR 找不到。
- 一个 Issue 多个 PR 时只取已合并的最新一个。
- 阈值（规模分档、巡检关键词）是经验值，写在 `export_samples.py` 顶部常量里。
- 导出不做脱敏；对外发布前需单独审查 `input.body`、`error_summary` 中是否含内部信息。
