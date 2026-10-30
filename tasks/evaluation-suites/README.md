# 评测集（Evaluation Suites）维护指南

本目录是 Console 模型评测使用的版本化 suite registry。每个 YAML 文件定义一批固定样本；可选资料放在同级 `<yaml 文件名去掉后缀>.d/<case_id>/`。run 冻结 suite 内容摘要、每题基线、执行预设与重复次数，后续文件变化不影响已有 run。

## 目录与兼容性

- Registry 只扫描 `*.yaml`；`example.yaml` 是旧格式样例，保持可直接运行。
- Suite `id` 在本目录唯一。内容变化需提升 `version`。
- 没有逐题 `base_ref`、`split`、判分资料等新字段的 v1 suite 继续按旧语义运行：每题沿用顶层 `base_ref`、重复一次且不要求校准。
- v2 suite 的资料变化会改变 `content_hash`，旧校准结果随之失效。

## 格式示例

```yaml
id: my-history-suite
version: 1.0.0
title: 历史修复基准
repo_path: .                 # 当前 Keda 检出根；也可用原有绝对路径
base_ref: main               # v2 case 可自行覆盖
rubric:
  id: default-rubric
  version: v1
check:
  timeout_seconds: 180
cases:
  - id: fix-example
    prompt: 修复该行为，使空输入返回空列表。
    check_recipe: uv-pytest
    base_ref: 0123456789abcdef0123456789abcdef01234567
    split: dev                # dev | holdout
    check_targets:
      - tests/test_example.py
    held_out:
      - tests/test_example.py
    candidate_hidden:
      - tasks/pending/known-answer.md
    gold_patch: gold.patch
```

`held_out` 是资料目录内相对于 `<case_id>/` 的测试/辅助文件路径列表；这些文件会在候选完成后整文件覆盖到快照。`candidate_hidden` 是基线仓库内的相对路径列表，用于移除已经确认会泄露答案的文件；候选结束后，执行器恢复这些文件的冻结基线版本。`gold_patch` 是同一 case 资料目录内的相对路径，保存原修复中非判分文件的零上下文补丁；补丁只应用到 SHA 和 tree hash 已冻结的基线快照。判分文件与 `candidate_hidden` 路径都不会在候选运行期间可见。`split` 缺省为 `dev`。

```text
my-history-suite.yaml
my-history-suite.d/
└── fix-example/
    ├── gold.patch
    └── held_out/
        └── tests/test_example.py
```

`check_targets` 写 pytest 文件路径或 pytest 节点 id，例如 `tests/test_example.py::test_empty`；不得写 Python 导入路径形式。解析器会拒绝资料路径逃逸、符号链接、以 `-` 开头的判分目标，以及不在安全字符集内的 target。`repo_path: .` 解析为 Keda 检出根；suite 也可继续使用绝对仓库路径。

## 校准和执行

候选使用导出的基线文件创建全新 Git 仓库，仓库只有一个提交且不共享来源仓库对象库。Agent 完成后，harness 先还原 `pyproject.toml` / `uv.lock`，再覆盖留出的判分材料并执行检查。`@noop` 不改源码，`@gold` 应用原始非测试修复；二者都不调用模型。

声明的 `candidate_hidden` 路径在候选启动前从基线快照移除；候选完成后，harness 丢弃这些路径上的候选改动并恢复冻结基线版本，再进行判分。该字段仅处理已确认存在于基线中的答案材料，不提供通用文件系统沙箱。

v2 suite 必须通过三次重复校准才能用于真实执行：每题 `@noop` 三次判分失败且 `@gold` 三次判分通过。校准同时绑定每题解析后的 commit/tree；符号 `base_ref` 移动后，必须在新基线上重新校准。校准 run 自动包含全部 suite 题目（含 holdout）；真实模型 run 默认只选 dev 题，纳入留出题时必须显式勾选。校准 run 要求重复次数至少 3，最多 5；模型候选执行总格数上限为 48，纯校准上限为 144。旧格式 suite 不要求校准，重复次数默认为 1。

固定检查 recipe：

- `pytest`：`python3 -m pytest -q`
- `uv-pytest`：`uv run --frozen pytest -q -o addopts=`，之后仅追加经过校验的 `check_targets`
- `unittest`：`python3 -m unittest discover -s tests`
- `node-test`：`npm test --silent`

判分失败与 Agent/工作区故障分开记录。重复结果按题和预设展示通过次数、故障次数与稳定性；稳定表示所有已完成重复的判分结论相同，不生成总分或合格阈值。

## 维护者校验

1. Console「Evaluations」页或 `GET /api/v1/agent-runner/console/evaluation/suites` 检查 suite 是否有效及逐题校准状态。
2. 为 v2 suite 创建 `@noop` 与 `@gold` 校准 run，重复 3 次；全部通过后才运行真实预设。
3. 检查 suite 的任务描述只包含问题和期望行为，不包含修复提示、修复提交或测试答案。

公开仓库可能允许 Agent 联网查看历史修复，模型也可能记住公开内容；快照隔离不能消除这类泄漏，因此留出集只用于相对变化判断。

## 首批 suite `keda-history-v1` 来源映射

`keda-history-v1.yaml` 的 12 题（8 dev + 4 holdout）冻结自 `keda-datasets` 数据版本 `c9cb2672364d4db26580498b490bf79e6d28e0b9`（`manifest_sha256` = `2a6585a1eeb3d9e273d133b76fa9e218d70df20166226caf50bb4577b768e5b0`）。suite YAML 文件摘要为 `48792e3aecf3cae29dbacb77186634cbe084b867bd822c1ceb2a08d41050191e`；Console 使用的 suite 内容 hash（YAML + 全部资料）为 `5f78f39cd2d604f79b3c98c00e68025312446c2967ea3237be1c1c3fb3cdfbea`。下表与 `tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-2-seed-selection.json` 同源（更细的 source 文件摘要、gold patch 路径与逐题资料清单见该 JSON 与 suite YAML）。`base → final` 是冻结修复的提交范围，用于核对 gold patch 来源；旧离线校准只用于筛题，最终以 Console 的 `@noop` / `@gold` 三次校准为准。

| case id | sample id | 输入来源、复核与 split 依据 | base → final | 合并日期 | 旧离线校准记录 |
|---|---|---|---|---|---|
| `supervisor-check-decisions` | `keda#issue-156` | 原始 Issue 正文；7 次 attempt、1 次 run，归 dev。 | `f6c51fdc8a58bfab1a21f3dcf28b8d4a80b1498e` → `3f324938af0c10512ac43ff6717b7e1c401faf6d` | 2026-09-29 | 数据集校准记录：9 个行为 fail-to-pass，gold 82 passed。 |
| `captured-process-final-output` | `keda#issue-306` | 认领时 Issue 快照；1 次 attempt、8 次 invocation、2 次 run，归 dev。 | `e17f599a14a243dcdd1e7fefec04aa42674a0655` → `7f25d483721e2d71d9a79a805b2ce8710dc0b23b` | 2026-10-22 | 数据集校准记录：1 个行为 fail-to-pass，gold 67 passed。 |
| `daemon-safe-logging` | `keda-main#issue-175` | 原始 Issue 正文；5 次 attempt、1 次 run，归 dev。 | `f0af2d60c18dc157feb918364552e31bf56d8290` → `04fb34a1a523dad613732453b73c4140562747e9` | 2026-10-03 | 数据集校准记录：2 个行为 fail-to-pass，gold 53 passed。 |
| `idea-local-wall-clock` | `keda#issue-239` | 原始 Issue 正文中的 CLI/idea-inbox 行为切片；2 次 attempt、9 次 invocation、1 次 run，归 dev。 | `155797a1f803a38f510eae6d3d8671fc5b25fc2d` → `1983b9a533dd358e023a412a2382d65c65cf8302` | 2026-10-12 | 数据集校准记录：1 个行为 fail-to-pass，gold 322 passed（其他失败不在本题目标内）。 |
| `short-term-memory-records` | `ZataZhang/keda#commit-5f1414c562` | 人工确认补全；样本级 attempt/invocation/run 均为 0。因涉及记忆与 skill 蒸馏，保守归 dev。 | `d0595887f838686cb21caacd503cf2ffe4310413` → `5f1414c562cf65f1fbc971c45243fa5f1be7267e` | 2026-10-20 | 本机预筛报告：2 个行为 fail-to-pass，gold 41 passed。 |
| `failure-handoff-across-claims` | `ZataZhang/keda#pr-154` | 原始 PR 描述中的行为需求；无样本级运行记录，但属于故障恢复流程，保守归 dev。 | `87ab96ee149eae7240f7ecd16eb1de81d7ef4dae` → `381fa8668003c85b53405037588fdda16a23a42d` | 2026-09-23 | 本机预筛报告：4 个行为 fail-to-pass，gold 82 passed。 |
| `generated-pr-content-contract` | `ZataZhang/keda#pr-173` | 原始 PR 描述中的行为需求；无样本级运行记录，涉及生成内容流程，归 dev。 | `076aa00eb684daec1f3ff28c101a53614cc00595` → `24b18bc0621ce66e36c95a67d94ff6299a57cfab` | 2026-09-29 | 本机预筛报告：5 个行为 fail-to-pass，gold 69 passed。 |
| `issue-create-ready-default` | `ZataZhang/keda#pr-261` | 人工确认补全；样本级运行记录为 0。因直接影响 Issue 创建/排队默认流程，保守归 dev。 | `0522db0839631c2c997d0641699519ba3ee7a44e` → `ab150c260dfaec3b1d81909e3ed9bff850056eb6` | 2026-10-09 | 数据集校准记录：1 个行为 fail-to-pass。 |
| `github-budget-ledger-failure` | `ZataZhang/keda#commit-3a064d11a2` | 人工确认补全；样本级 attempt/invocation/run 均为 0。行为不涉及提示词、技能或预设调优，归 holdout。 | `51faf140236c84d64f24d7721a2afe779ed182c4` → `3a064d11a2a95d0ac1f06d7cfb5115415a01f995` | 2026-10-16 | 数据集校准记录：3 个行为 fail-to-pass，gold 33 passed。 |
| `issue-list-table-output` | `ZataZhang/keda#pr-166` | 原始 PR 描述；样本级 attempt/invocation/run 均为 0，属于独立 CLI 展示行为，归 holdout。 | `947fd1e6f7ab1738683aeba977d535b3b76f73e0` → `363c7d6faaff60a24b2e459cb6276ef5953c1d22` | 2026-09-29 | 本机预筛报告：2 个行为 fail-to-pass，gold 20 passed。 |
| `worktree-env-run-state` | `ZataZhang/keda#pr-189` | 原始 PR 描述；样本级 attempt/invocation/run 均为 0。无提示词/技能/预设调优记录，归 holdout。 | `13641cdce5cc095953f7f002659e030b6323ef11` → `ebc88c815cff2194c07c0a9f6637d3dccce20cd2` | 2026-10-04 | 本机预筛报告：1 个行为 fail-to-pass，gold 8 passed。 |
| `token-usage-by-prd` | `ZataZhang/keda#pr-211` | 原始 Issue 正文；样本级 attempt/invocation/run 均为 0，属于 Stats 展示数据聚合，不影响提示词/技能/预设，归 holdout。 | `d486dbdc9f0a41a14b9a572425a11f9b7161c226` → `55c759ea9e3e13e2b76b41ba17a74704a764c05e` | 2026-10-06 | 数据集校准记录：黑盒失败目标 1；本 suite 扩展为 PRD 维度聚合与降级行为测试。 |

公开材料只含 keda 已合并任务，私有数据集原始归档未复制进仓库。`candidate_hidden` 逐题列在 suite YAML；其中 `idea-local-wall-clock` 声明的 `docs/ai-standards/time-display.md` 在基线上不存在，删除为无操作。

## 隐私和外发

- 不写入私有 Issue 正文、客户代码、凭据或本机数据路径；prompt 仅作最小的问题/期望行为复述。
- 评审 payload 仍须经页面预览并逐次授权；本里程碑不改变评审授权流程。
