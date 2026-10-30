# 评测与测试

本文档统一项目测试与 AI 能力评测实践。评测功能（多执行预设 × 多评审模型的
Agent/模型效果对照）已落地为一条本地 Console 产品路径，下文记录其口径与边界。

## 测试命令

- 本地测试：`just test`
- 全量测试：`just test all`
- 真实依赖测试：`just test real`

## 测试分层建议

- **单元测试**：聚焦函数级逻辑，快速反馈。
- **集成测试**：覆盖模块协作边界。
- **真实环境测试**：针对依赖外部 API 的能力进行验证。

## 模型评测（Console「Evaluations」页）

### 定位与语义

- 评测是**手动、本机发起**的对照任务。旧格式 suite 沿用单一 `base_ref` 与单次
  执行；v2 suite 可为每题冻结独立基线并重复运行。候选在从基线文件导出的单提交
  仓库中执行，该仓库不共享来源仓库的 Git 对象库。
- 结果**只作比较，不作验收**：分数用于横向观察 Agent/模型表现，不构成任何
  Issue 或 PRD 的完成证据。
- 确定性检查（checks）与评审评分**分列存储、互不折算**：check 结论
  passed/failed/timeout/not_run 不产生分数，评分也不改写 check 结论。
- 评审复用现有 Agent 执行器，**不引入 provider SDK、API key 或额外依赖**：评审预设
  就是 `[agent_runner.presets.*]`，调用走与候选执行同一个进程边界，认证沿用各 agent
  CLI 自己的配置（如 `~/.claude`）。
- 评审隔离：agent 的 `judge` profile 声明**禁用全部内置工具**（claude / codebuddy /
  qoder 为 `--tools ""`，pi 为 `--no-tools`），且进程 cwd 是每次新建的空临时目录，
  评审只能读到授权预览过的 payload。没有该开关的 agent（codex、kimi、opencode）
  不声明 `judge` 形态，评测页把它们标为"不可用"并给出原因，创建 run 时直接拒绝，
  不假装隔离。注意：agent CLI 仍会联系其自己的模型服务，这正是外发授权要覆盖的范围。

### 评测集（suite）

- 版本化 YAML 存放在 `tasks/evaluation-suites/`，字段规范与维护流程见该目录的
  `README.md`（样例：`tasks/evaluation-suites/example.yaml`）。
- 必填字段：`id`、`version`（semver）、`repo_path`、`base_ref`、非空 `cases`；
  每条 case 含 `id`（`^[a-z0-9][a-z0-9._-]{0,79}$`）、`prompt` 与白名单
  `check_recipe`（pytest / uv-pytest / unittest / node-test）。v2 case 还可指定
  `base_ref`、`split`、`check_targets`、`held_out`、`candidate_hidden` 与 `gold_patch`；资料存于 YAML
  同级 `<stem>.d/<case_id>/`，资料内容也参与 suite hash。`repo_path: .` 表示当前
  Keda 检出根。`check_targets` 使用 pytest 文件路径或节点 id（如
  `tests/test_example.py::test_empty`），不使用 Python 导入路径写法。
- suite `id` 在 registry 目录内必须唯一：重复定义会让两个文件一起被判
  invalid（清单接口与页面都据此标红），因为此时「按 id 加载」无法确定取哪个
  文件，历史 run 的 `suite_hash` 也会失去唯一解释。
- run 创建时冻结 suite 内容 hash、base commit 与全部预设快照；此后修改
  suite 文件或 config.toml 不影响历史 run 的解释口径。
- v2 候选 Agent 运行期间看不到判分资料或 suite 声明的已知泄题路径。Agent 结束后，执行器先还原
  `held_out`、`candidate_hidden` 与基线中的 `pyproject.toml` / `uv.lock`，再整文件覆盖
  `held_out` 测试材料并运行固定 recipe；
  `@noop` 与 `@gold` 校准候选不启动模型。任一逐题材料改变都会改变 suite hash，
  先前的校准不能复用。
- v2 校准 run 自动覆盖全部 suite 题目（含 holdout），不需要额外勾选；真实模型候选
  仍仅默认选择 dev 题，纳入 holdout 时必须显式勾选。
  真实候选必须使用已完成的三次 `@noop` / `@gold` 校准题。
  重复次数范围为 1–5，校准至少 3 次；模型候选矩阵最多 48 格，纯校准最多 144 格。
  汇总按题和预设展示通过次数、执行故障数与稳定性，不折算总分，也不设阈值。
- 冻结口径是**执行前守卫**而不只是展示：候选执行前核对 live 执行预设、评审
  调用前核对 live 评审预设，任一字段（模型、agent、端点、密钥变量名等）与快照
  不一致即停止——候选落 `failed`、评审落 `provider_error` 且**零外发**，避免
  「页面显示 A、实际跑 B」的不可解释对照。
- 并发上限同时受 `evaluation.max_concurrency` 与 runner 的
  `max_concurrent_issues` 约束。矩阵池的上限是 **per-run** 的，因此候选真正
  落地（worktree + Agent 子进程 + check）另过一道进程级闸门：多个 run 同时
  在跑时，主机上的在飞候选数仍是同一个 ceiling，而不是 N × ceiling。排队拿到
  槽位后会重新确认 run 仍在 `running`，期间被取消的 run 不会补发 Agent 调用。

### rubric v1（default-rubric@v1）

四个维度，每维 1–5 整数分 + 简短理由：

| 维度 | 含义 |
|---|---|
| `correctness` | 正确性 / 任务要求 |
| `completeness` | 完整性与范围 |
| `implementation_quality` | 实现质量 |
| `verification_evidence` | 验证证据 |

评审响应必须通过 schema 校验：缺维、非整数、越界、多余维度、无法解析、
显式拒答分别落 `invalid` / `refusal` 状态；调用超时与 provider 错误落
`timeout` / `provider_error`；授权前 deny / 取消分别落 `unauthorized` /
`cancelled` 留痕。**任何失败态都不折算为 0 分**，页面按状态桶如实展示。

### 汇总口径（版本/hash 跟踪与指标）

- 每维只从 `valid` 评分行派生 **中位数 / min / max / N**（聚合不落库，
  永远可从逐评审明细重算）。
- N=1 时只显示该评审者的分数与「1 位评审」，不出现「评审一致」表述；
  分歧通过展开逐评审分数与理由查看。
- 未完成候选带各自状态，页面不得把未完成矩阵过滤后仍称全批次完成。
- 成本口径：当前统一标注 `cost unknown`，不把未核实的价格写进结果。

### 评审授权流程（外发边界）

1. 候选执行完成（或被拒绝评审）之前，**零评审调用**。
   `POST start` 只排程候选执行。
2. 执行完成后 run 进入 `awaiting_judge_consent`：payload（任务说明、执行摘要、
   check 结论、代码 diff）只暂存本机私有临时目录。
3. 页面预览与实际发送内容**逐字节一致**的 payload，并展示 manifest hash
   （预览上限覆盖 payload 最坏尺寸，极端超长内容截断时显式标记，hash 仍绑定全文）。
4. 显式 approve 后，服务端重新核对请求 hash、run 冻结 hash 与当前暂存文件
   重算 hash，三者一致才发起评审调用；hash 过期返回冲突并要求重新预览。
5. deny 则 run 收尾为 completed，并为每个候选 × 评审者写入 `unauthorized`
   评分行（页面据此区分「评审者被拒绝」与「评审者缺席」），同时清除暂存
   payload。
6. 在 `awaiting_judge_consent` 上取消（cancel）同理写入 `cancelled` 评分行：
   两类留痕行的分数域一律为空，只有真实评审才能产生 `valid` 分数。

### 数据保留与删除

- 持久层（Console SQLite，`PRAGMA user_version` v16）只存结构化摘要：
  不保存 raw prompt、完整 patch、token 用量原文、环境变量或密钥值。
- 暂存 payload 与隔离 worktree 在评分完成、拒绝、取消或删除 run 时清除；
  删除 run 级联清除其结果与评分行。强制删除仍在执行的 run 时，在飞线程的
  残余回写按 run 存在性被丢弃，评审派发在调用前自检 run 仍在 `judging`，
  不会留下孤儿行，也不会为新删除的 run 追加评审调用。矩阵收尾同理：
  并发取消/删除若抢在「进入待授权」这次状态比较并交换之前落地，刚写入的
  manifest 与候选 diff 会立即清除，不会给停在 `cancelled` 的 run 留残骸。
- Console 重启自愈：上次进程遗留的活跃 run 一律标 `interrupted`，并一并
  删掉其暂存 payload（自愈后这些 run 再也走不到授权或拒绝，留下的只会是
  无人负责的代码差异）；隔离 worktree 的目录清理仍归「删除 run」，避免把
  git 调用压在启动路径上。不提供续跑，由用户新建 run 重跑或删除，绝不自动
  重跑（避免不可解释的重复外部调用副作用）。

### v16 重复账本与泄漏边界

- `evaluation_run.repeats` 保存重复次数，`case_bases_json` 保存每题创建 run 时冻结的
  `{base_ref, commit, tree}`。
- `evaluation_result.repeat_index` 从 0 开始。旧记录迁移为第 0 次；新唯一约束为
  `(run_id, case_id, execution_preset_id, repeat_index)`，旧结果和评分行保留。
- 单提交快照不防止公开 GitHub 历史被 Agent 联网读取，也不防止模型记忆修复；
  对留出题只解释相对变化，不把绝对通过率作为未泄漏的估计。

### 无凭据验证入口

评审调用是唯一的外部边界；其余链路可在**没有任何 agent 登录态**的情况下验证：

- suite 校验：`GET /api/v1/agent-runner/console/evaluation/suites`（页面清单同源）。
- run 创建/启动/取消/删除、payload 预览与 deny 路径均不需要凭据；judge 预设
  可用性探针只检查"agent 是否声明 judge 形态、CLI 是否在 PATH 上"，不起进程、
  不发起网络调用。
- 授权前的「零外发」可用本地请求计数负控复验（见
  `tasks/evidence/P1-FEAT-20261012-103909-multi-judge-agent-evaluation/`）。

## 建议指标

- 正确率或任务完成率
- 响应时延
- 稳定性与失败率
- 成本指标（按调用量或 token 统计）

## TODO

- TODO: rubric v2（维度或分档定义演进）及其与历史 run 的对照换算。
- TODO: 若接入价格事实源，替换统一的 `cost unknown` 口径。
