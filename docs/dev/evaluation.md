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

- 评测是**手动、离线、本机发起**的对照任务：同一 suite、同一 `base_ref` 基线下，
  多个执行预设（agent × 模型 × 推理档）各自在**隔离 git worktree** 中跑同一批
  样本，再由多个评审模型独立打分。
- 结果**只作比较，不作验收**：分数用于横向观察 Agent/模型表现，不构成任何
  Issue 或 PRD 的完成证据。
- 确定性检查（checks）与评审评分**分列存储、互不折算**：check 结论
  passed/failed/timeout/not_run 不产生分数，评分也不改写 check 结论。
- 运行时依赖 `llm` extras：judge 调用走 `create_chat_model`，无条件 import
  langchain provider SDK；新环境先 `uv sync --extra llm`。

### 评测集（suite）

- 版本化 YAML 存放在 `tasks/evaluation-suites/`，字段规范与维护流程见该目录的
  `README.md`（样例：`tasks/evaluation-suites/example.yaml`）。
- 必填字段：`id`、`version`（semver）、`repo_path`、`base_ref`、非空 `cases`；
  每条 case 含 `id`（`^[a-z0-9][a-z0-9._-]{0,79}$`）、`prompt` 与白名单
  `check_recipe`（pytest / unittest / node-test）。
- run 创建时冻结 suite 内容 hash、base commit 与全部预设快照；此后修改
  suite 文件或 config.toml 不影响历史 run 的解释口径。
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

1. 候选执行完成（或被拒绝评审）之前，**零 provider 评审调用**。
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

- 持久层（Console SQLite，`PRAGMA user_version` v15）只存结构化摘要：
  不保存 raw prompt、完整 patch、token 用量原文、环境变量或密钥值。
- 暂存 payload 与隔离 worktree 在评分完成、拒绝、取消或删除 run 时清除；
  删除 run 级联清除其结果与评分行。强制删除仍在执行的 run 时，在飞线程的
  残余回写按 run 存在性被丢弃，评审派发在调用前自检 run 仍在 `judging`，
  不会留下孤儿行，也不会为新删除的 run 追加 provider 调用。
- Console 重启自愈：上次进程遗留的活跃 run 一律标 `interrupted`；不提供
  续跑，由用户新建 run 重跑或删除，绝不自动重跑（避免不可解释的重复
  provider 副作用）。

### 无凭据验证入口

评审模型调用是唯一的外部边界；其余链路可在**没有任何 API key** 的情况下验证：

- suite 校验：`GET /api/v1/agent-runner/console/evaluation/suites`（页面清单同源）。
- run 创建/启动/取消/删除、payload 预览与 deny 路径均不需要凭据；judge 预设
  可用性探针只查本地凭据链是否可解析，不发起网络调用。
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
