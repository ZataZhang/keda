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
| `git_commit` | `--commits-repo <本地仓库>`：默认分支上没有 PR、也没被其他来源覆盖的普通提交 | 只有提交信息（`commit_message`，重建的） | 无 |
| `agent_session` | `--agent-sessions-repo <本地仓库>`（Claude Code）或 `--codex-sessions-repo <本地仓库>`（codex）：该仓库的真人会话 | 会话里用户**逐字**的首句，脱敏后导出（`session_first_prompt`） | 无（不导出完整轨迹） |

```bash
# 额外导出一个仓库里与 keda 无关的已合并 PR（需要 gh 已登录；可重复；不依赖 console.db）
uv run python -m scripts.dataset_export --github-pr-repo ~/code/some-project --pr-limit 100 --output samples.jsonl
```

- 跳过机器人账号（如 Dependabot）的 PR，以及 head 分支为 `issue-N` 且该 Issue 已作为 `keda_run` 样本导出的 PR，避免重复。
- **`pr_description` 不能当评测题**：PR 描述写的是"改了什么"，不是任务本身，等于泄露答案；这类样本只作重建记录保留，永远不计入 `eval_ready`。要让非 keda 的任务成为合格评测题，需要它有关联的 Issue（PR 描述里写 `Closes #N`）。
- 非 keda 来源没有 attempt，所以 `has_preference_pair` 恒为 `false`，不能产出偏好对。
- 为避开 GitHub GraphQL 的节点上限，先列出轻量信息筛选，再逐个取剩下 PR 的详情；每个 PR 至少一次网络调用（有关联 Issue 再加一次），80 个 PR 大约一分多钟。

### 普通提交（`git_commit`）

默认分支上既没有 PR、也没有会话记录的提交，例如直接推到默认分支的改动：

```bash
uv run python -m scripts.dataset_export --commits-repo ~/code/some-project --commit-limit 100 --output samples.jsonl
```

- **默认分支**优先读远程声明（`refs/remotes/<远程>/HEAD`），取不到再试 `origin/main`、`main`、`origin/master`、`master`——很多项目的默认分支不叫 main（例如博客仓库是 `hugo`）。
- 沿默认分支的**第一父链**取最近的提交，排除：合并提交、标题带 `(#N)` 的 squash-merge PR 提交、机器人账号（`[bot]`、dependabot、github-actions）的提交，以及已经被 keda 运行、PR、会话样本覆盖的提交（按提交哈希判断），避免同一件事导出两次。
- 一个样本就是一个提交：`code.base_sha` 是它的父提交，`final_sha` 是它自己。
- **质量最低，永远不计入 `eval_ready`**：任务输入只能从提交信息重建，既不是任务本身，也没有尝试与轨迹。作者姓名与邮箱不导出；提交正文经过同样的脱敏。它的价值在于保留事实，并通过下面的"后期补全"变成真正的题。

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

## 后期补全（curation）

数据集会不断优化。输入偏弱的样本（`pr_description`、`session_first_prompt`、`commit_message`、`title_only` 等）靠后期的人或更强的 Agent 补成真正的任务描述。补全是**叠加在原始记录之上的补丁，永远不改原始记录**：

```bash
# 1. 导出，同时生成待补全清单
uv run python -m scripts.dataset_export --commits-repo ~/code/proj --curation-worklist worklist.jsonl --output samples.jsonl
# 2. 人或 Agent 根据清单写补全记录，放进一个目录（以后可以是数据集仓库）
# 3. 带上补全目录再导出，补全被叠加上去
uv run python -m scripts.dataset_export --commits-repo ~/code/proj --curation-dir ~/datasets/curation --output samples.jsonl
```

**待补全清单**（`--curation-worklist`）：每行是一个输入偏弱、开发类、有提交、没被排除的样本，带上已脱敏的当前输入、提交主题、起点与终点、规模和链接，足够让人或 Agent 写出不泄露实现方式的任务描述。清单里没有代码本身。

给出本地仓库参数（`--commits-repo` / `--github-pr-repo` / `--agent-sessions-repo` / `--codex-sessions-repo` 任一）后，对应仓库的条目还会带上这些**提示**（不是结论，由补全员或人最终判断）：

| 字段 | 内容 | 用来判断什么 |
|---|---|---|
| `change_context.changed_files` / `changed_file_count` | 改动的文件（最多 60 个）与总数 | 题目范围有多大 |
| `change_context.files_by_area` | 按"前两级目录"分组的文件数 | 是否夹带了与主题无关的改动（区域五花八门往往就是夹带） |
| `change_context.test_files` / `new_files` | 改动的测试文件；新增的非测试文件 | 有没有测试、加了哪些新模块 |
| `change_context.new_symbols_referenced_by_tests` | 这次改动**新增**的函数、类、常量、模块名中，被测试新增行引用到的 | 测试是否依赖实现细节 |
| `change_context.check_recipe_hint` | `tests_use_existing_entry_points`（测试只走已有入口，可考虑用测试判分）/ `tests_use_new_symbols`（测试直接引用新增符号，任务描述又不能写出这些名字，用测试判分对做题的人不公平，通常不要填 `check_recipe`）/ `no_tests` / `only_test_changes`（只改了测试，通常不构成一道题） | 能不能填 `check_recipe` |
| `closing_issue_numbers` | 从提交主题、提交正文或 PR 描述里解析出的 `Closes/Fixes/Resolves #N` | 原始需求可能写在哪个 Issue |
| `closing_issues` | 仅在同时带 `--with-gh` 时：这些 Issue 的标题与正文（正文截断到 4000 字符） | 大改动的原始需求 |

符号依赖靠对 diff 的文本匹配：会漏（比如通过反射间接引用）也会误报（同名的已有符号），所以只当提示。在 keda 自己的 10 个试跑样本上，提示与补全员人工判断的"能否用测试判分"完全一致（8 个非排除样本：2 个能、6 个依赖新增符号）。

**补全记录**是 `--curation-dir` 目录下的 `*.jsonl`，每行一条，只追加：

```json
{"schema_version": 1, "sample_id": "owner/repo#commit-1a2b3c4d5e",
 "at": "2026-10-22T08:00:00Z",
 "by": {"kind": "agent", "name": "curator", "model": "<模型名>"},
 "reviewed_by_human": false,
 "input": {"title": "…", "body": "…"},
 "exclude": false, "check_recipe": "pytest", "notes": "…"}
```

- `by.kind` 是 `human` 或 `agent`；`input` 只能含 `title`、`body`；`exclude: true` 把题排除；`check_recipe` 记录这道题怎么判分。同一样本可以有多条，**同一字段以 `at` 最晚的为准**。
- 补全过输入的样本：`input.provenance` 变成 `curated_human`（人补的）、`curated_agent_reviewed`（Agent 补且人复核过）或 `curated_agent`（Agent 补、未复核）；**原来的输入保留在 `input.original`**；样本多出 `curation`（条数、最后一次是谁、`check_recipe`、备注、是否排除）。
- **Agent 补的在人复核之前仍算输入偏弱，不计入 `eval_ready`**；补全后会重新计算 `eval_ready`，被排除的永远不计入。
- 补全文本同样经过脱敏，`input.redaction_count` 会累加。
- 无效行（格式不对、版本未知、字段不认识）跳过并告警；找不到对应样本的补全记数打印到 stderr，不会报错。
- **样本 id 要稳定，补全才能一直对得上**：PR 样本用 `owner/repo#pr-N`，提交样本用提交哈希前 10 位，会话样本用会话 id 后 8 位；只有改写历史（rebase、amend）才会让提交样本的 id 变化。

## 归档到数据集仓库（`--archive-dir`）

本机的快照、会话记录、运行记录都可能丢失或被清理，所以要定期把导出的样本归档进一个数据集仓库长期保存：

```bash
uv run python -m scripts.dataset_export --with-gh --repo keda --commits-repo ~/code/keda --archive-dir ~/code/keda-datasets
```

- **每个样本一个文件**：`raw/<来源>/<仓库>/<样本 id>.json`（路径分隔符、`#` 等都换成下划线）。原地更新，版本靠 git 历史保存，所以每天定期导出不会像"每次复制一份全量文件"那样让仓库膨胀。
- **序列化稳定**：键排序、固定缩进、样本里不含导出时间；内容没变，文件就逐字节不变，git 里没有噪音。命令结束会打印"新增 / 更新 / 未变"各多少个。
- **只增不删**：本地来源消失（会话被清理、运行记录被删）后，归档里已有的样本继续保留。这是归档存在的意义。
- `MANIFEST.json` 只记各来源的数量，不含时间。
- **不碰补全层**：补全记录放在数据集仓库的 `curation/` 里，用 `--curation-dir` 叠加；归档只写 `raw/`。
- 给了 `--archive-dir` 又没给 `--output` 时，不再把样本打到 stdout。
- 本工具只写文件，不做 git 操作；提交与推送由调用方（定时脚本）负责。
- **隐私兜底**：写入之前先检查**全部**样本，只要有一个还带着邮箱、本机家目录路径、常见令牌或私钥，就拒绝归档且一个文件都不写（退出码 4，只列种类和样本 id，不列具体值）。宁可让定时任务失败，也不把个人信息写进长期保存的仓库。

**退出码**：`0` 成功；`3` 有被要求的来源被跳过（例如 `gh` 偶发失败、找不到默认分支）——样本与归档已经写好、数据有效，但**不能报成功**，所以单独用 3；`4` 归档被隐私兜底拦下，什么都没写。`gh` 调用失败会先重试几次（GitHub 的接口会偶发 499/5xx），仍失败才算跳过。

**哪些内容该进归档，要在调用时用参数控制**：归档里是任务原文与提交信息，会话首句还是用户的原话（脱敏只是模式匹配）。没有明确同意之前，不要把公司仓库、会话记录归档进个人账号下的仓库。

### 定时归档（`archive_daily.sh`）

`scripts/dataset_export/archive_daily.sh` 把"导出 → 归档 → 有变化就提交并推送"串成一次可定时执行的任务。**它是确定性脚本，不要用 `kc loop` 来跑**：`kc loop` 是"定时开 Issue、交给 Agent 走 PR 流程"，每天为一次归档启动一个 Agent 既浪费又不可靠。

- 用一个**专用的只读工作树**（默认 `~/.kedacode/dataset-runner/keda`，固定在 `zata/main`）跑导出，不用你正在改的检出：你改到一半的代码不会让定时任务失败，它也不会碰你的工作区。这个工作树会出现在你仓库的 `git worktree list` 里。
- 导出失败就不提交，不会把残缺数据写进归档；只提交 `raw/` 与 `MANIFEST.json`，`curation/` 是人工补全，由人手工提交。
- 运行锁防止重叠；锁里存 PID，进程已不在就当陈旧锁清掉。
- **默认只归档本人名下的仓库，不含公司仓库，也不含会话记录**。要加入需要先单独确认；用环境变量 `COMMIT_REPOS`、`PR_REPOS`、`KEDA_RUN_REPO_IDS` 覆盖仓库列表，路径用 `KEDA_DIR`、`DATASET_DIR`、`RUNNER_DIR` 覆盖。

macOS 上用 launchd 每天定时运行（路径按你的机器改；`PATH` 要写全，launchd 的环境很干净）：

```xml
<!-- ~/Library/LaunchAgents/com.kedacode.dataset-archive.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.kedacode.dataset-archive</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>/Users/<你>/.kedacode/dataset-runner/keda/scripts/dataset_export/archive_daily.sh</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>/Users/<你>/.kedacode/logs/dataset-archive.log</string>
  <key>StandardErrorPath</key><string>/Users/<你>/.kedacode/logs/dataset-archive.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/Users/<你>/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict></plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kedacode.dataset-archive.plist   # 启用
launchctl kickstart gui/$(id -u)/com.kedacode.dataset-archive                                # 立刻跑一次
launchctl bootout gui/$(id -u)/com.kedacode.dataset-archive                                  # 停用
tail -f ~/.kedacode/logs/dataset-archive.log                                                 # 看日志
```

电脑在 03:30 休眠时，launchd 会在唤醒后补跑；关机则错过当天。归档是增量的，错过一天不影响下一次把变化补齐。

## 校准判分器（`calibrate`）

一道评测题能成立，要求**判分器本身可靠**。`python -m scripts.dataset_export.calibrate` 对候选题做"失败转通过"检验，**不调用任何模型**：

| 运行 | 代码 | 测试 | 期望 |
|---|---|---|---|
| gold（应用了原始修复） | 改动后 | 改动后 | 通过 |
| noop（什么都不改） | 改动前 | 改动后（含 `tests/` 下新增的辅助文件，已删除的也删掉） | 有测试失败 |

noop 下失败、gold 下通过的测试是 `fail_to_pass`（做对了才会变绿）；两边都通过的是 `pass_to_pass`（不能被改坏）。**黑盒的 `fail_to_pass`** 还要求 noop 下失败的**原因**是断言（行为不对），而不是 `ImportError`、`AttributeError` 这类"找不到这次新增的内部名字"——后者说明测试与实现细节耦合，任务描述又不能写出那些名字，用它判分对做题的人不公平。

| `status` | 含义 |
|---|---|
| `calibrated` | 至少有一个黑盒 `fail_to_pass`：判分器可用 |
| `coupled_to_new_symbols` | 有 `fail_to_pass`，但全部只因找不到新增的名字而失败 |
| `no_fail_to_pass` | 改动前的代码也能通过这些测试，区分不出做没做对 |
| `gold_failed` | 应用了原始修复后测试也没通过，判分器本身不可用 |
| `no_tests` | 这次改动没有新增或修改任何 Python 测试文件 |
| `error` | 环境问题（提交不在本地、pytest 崩溃或超时）；下次会重跑 |

```bash
uv run python -m scripts.dataset_export.calibrate \
  --raw-dir ~/code/keda-datasets/raw --out-dir ~/code/keda-datasets/calibration \
  --repo keda=~/code/keda --repo keda-main=~/code/keda --repo ZataZhang/keda=~/code/keda --jobs 2
```

- 默认只校准机器判定的候选题（`eval_ready`）；`--all` 校准全部，`--sample-id` 指定样本，`--force` 重跑已有记录。同一对提交已有非 `error` 的记录时自动跳过。
- 每个样本写一个 `calibration/<样本 id>.json`：`status`、`fail_to_pass`、`black_box_fail_to_pass`、`pass_to_pass`、`gold_failures`、两次运行的结果计数、被校准的两个提交。这是**运行事实**，不是人的判断，所以单独存放，不混进补全层。
- **不碰源仓库**：用 `git archive` 把两个提交导出到临时目录，不建 worktree、不改任何引用；测试子进程的 `HOME` 指向临时目录，早期提交里会写 `~/.kedacode` 之类的测试也弄脏不了真实的本机状态。
- 运行测试用 `uv run python -m pytest`（`--command` 可改），`-o addopts=` 清掉 `--testmon`，并加 `--continue-on-collection-errors`：改动前有的测试文件会因为找不到新增的名字而无法导入，不能让它中止整次运行，否则同一次改动里其他文件的行为测试也跑不出来。

**它证明什么、不证明什么**：`calibrated` 只说明"用这些测试判分，改动前红、改动后绿，且红是因为行为不对"。它**不**证明任务描述足够清楚、不证明题目不泄露答案、不证明测试没有偶发失败（gold 只跑一次）；`pass_to_pass` 也只覆盖这次改动涉及的测试文件，不是整套测试。所以 `calibrated` 离"合格评测题"还差**人工复核**，并且只支持 Python 测试。

## 样本里有什么个人信息，没有什么

导出器**不**把这些放进样本：提交作者的姓名、登录名、邮箱（PR 对象只保留判定"已合并"与还原提交链所需的白名单字段）；本机绝对路径（`/Users/<名>`、`/home/<名>` 一律替换为 `~`）；常见令牌、私钥与"键=值"形式的密钥；文本里的邮箱。所有样本的自由文本（任务标题与正文、错误摘要、提交主题）在导出最后统一再过一遍脱敏，这一步幂等；`input.redaction_count` 记录累计命中数，`input.redaction_reviewed` 恒为 `false`。

**这只是基于模式的脱敏，不保证抓全**（自然语言里的口令、内部系统名、客户名、人名都抓不到）。Issue 正文标为 `verbatim_issue_body` 时，含义是"逐字，只替换已识别的敏感模式"。

## 标签

| 标签 | 含义 |
|---|---|
| `resolved` / `resolved_basis` | 可信的依据有两种：`pr_merged`（关联 PR 已合并）与 `on_default_branch`（提交已在默认分支上，用于会话与普通提交样本）；`run_completed` 只表示 runner 跑完，**不代表已合并**，仅在拿不到 PR 时作兜底，不算可信。 |
| `task_kind` | 标题含"巡检/巡查"的自动巡检为 `patrol`；Conventional Commits 的 `docs` / `chore` 类标题（归档 PRD、同步模板等）为 `maintenance`；其余为 `development`。只有 `development` 才可能 `eval_ready`。 |
| `size_band` | `small`（≤5 文件且 ≤300 行）/ `medium`（≤30 文件且 ≤3000 行）/ `large`；规模未知为 `null`。 |
| `merge_commit_count` | base..final 间的 merge commit 数；非 0 说明混入了别的分支的改动；提交对象不在本地时为 `null`（未核实）。 |
| `has_commits` / `has_evidence` | 有提交链 / 有 `evidence.json`。 |
| `has_preference_pair` | 存在先失败、后成功的 attempt。 |
| `eval_ready` | 下一节的组合条件。 |

## 摘要中的 `eval_ready`

同时满足以下条件的样本：已解决（`resolved_basis` 为 `pr_merged` 或 `on_default_branch`）、有提交、输入可信（不是 `missing`、`pr_description`、`session_first_prompt`、`commit_message`，也不是未复核的 `curated_agent`）、`task_kind=development`、`size_band` 不是 `large`、`merge_commit_count` 为 0（`null` 即未核实，不算通过，会计入摘要里的 `unverified_history`）。巡检类样本仍会出现在输出里（便于统计），只是不计入 `eval_ready`。**不加 `--with-gh` 时 keda 运行样本的 `eval_ready` 恒为 0**，因为没有 PR 合并状态就无法确认任务真的解决了；会话、普通提交样本在补全前也恒为 0。评测集还需要每个样本有 `check_recipe`，这一步目前仍需人工补。

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
