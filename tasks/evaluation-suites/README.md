# 评测集（Evaluation Suites）维护指南

本目录是模型评测功能的**版本化评测集 registry**：Console「Evaluations」页创建
run 时，从这里加载 YAML 评测集。每个文件是一份可复验的对照样本清单——同一
`base_ref` 基线下，用多个执行预设跑同一批样本，再由多个评审模型独立打分。

约定：评测结果**只作比较，不作验收**；样本、基线与 rubric 全部显式入档，
保证任何人在本机可以重跑同一矩阵。

## 目录约定

- 仅 `*.yaml` 文件会被 registry 扫描（`example.yaml` 是样例，不是可执行样本集）。
- 一个文件定义一个 suite；**同一目录内 suite `id` 不允许重复**，重复会导致
  两个文件同时校验失败。
- 内容变更必须升 `version`（semver）：run 会冻结 `suite_hash`，改文件不会
  影响历史 run 的解释口径。

## 字段规范

```yaml
id: my-suite              # 必填，suite 标识（run 列表与详情按它归组）
version: 1.0.0            # 必填，semver；样本内容任何变化都要升版本
title: 我的评测集          # 可选，展示名；缺省用 id
repo_path: /path/to/repo  # 必填，样本所属仓库的本地路径（候选执行在它的隔离 worktree 中进行）
base_ref: main            # 必填，基线 ref；创建 run 时解析为具体 commit 并冻结
rubric:                   # 可选；当前仅支持 default-rubric@v1，写出其他值会校验失败
  id: default-rubric
  version: v1
check:
  timeout_seconds: 60     # 可选，正整数；上限 300，超出自动收敛到上限
cases:                    # 必填，非空
  - id: case-a            # 必填，匹配 ^[a-z0-9][a-z0-9._-]{0,79}$，suite 内唯一
    prompt: 任务描述文本    # 必填，发给执行预设 Agent 的原始提示词
    check_recipe: pytest  # 必填，白名单之一：pytest | unittest | node-test
```

校验入口（维护者自查，无需凭据）：

1. Console「Evaluations」页顶部的评测集清单会展示每个文件的
   `valid` / `errors`；或
2. `GET /api/v1/agent-runner/console/evaluation/suites`，返回逐文件校验结果。

## 受限的确定性检查（check recipe）

`check_recipe` 不是任意命令：候选执行完成后，在隔离 worktree 内按白名单配方
运行确定性测试，结论（passed / failed / timeout / not_run）与评审
评分**分列存储**，互相不折算。

- `pytest`：`python3 -m pytest -q`（worktree 内）
- `unittest`：`python3 -m unittest discover -s tests`
- `node-test`：`npm test --silent`

样本仓库应在基线内自带可运行的测试；配方失败/超时记为对应状态，绝不进入
评分聚合。

## rubric v1

固定四维，每维 1–5 整数分 + 简短理由：

| 维度 id | 含义 |
|---|---|
| `correctness` | 正确性 / 任务要求 |
| `completeness` | 完整性与范围 |
| `implementation_quality` | 实现质量 |
| `verification_evidence` | 验证证据 |

评审响应必须过 schema 校验：缺维、非整数、越界、多余维度、无法解析、拒答
分别落 `invalid` / `refusal` 等状态，**任何失败态都不折算为 0 分**；汇总
（每维中位数 / min / max / N）只从 `valid` 评分行派生。

## 隐私与外发边界

- suite 文件不要写入私有 Issue 正文、客户代码片段或凭据；prompt 用最小复述。
- 评审 payload（含候选 diff）默认只暂存本机；外发前必须在页面上看到**与实际
  发送逐字节一致**的完整 payload 并显式授权（approve/deny）。拒绝时零外发。

## 新增一个评测集

1. 复制 `example.yaml`，改 `id` / `repo_path` / `base_ref` / `cases`。
2. 用上面的校验入口确认 `valid: true`。
3. 在 Console 创建 run（此时不产生任何 provider 调用），检查冻结快照中的
   suite 版本与基线 commit 后，再显式启动执行。
