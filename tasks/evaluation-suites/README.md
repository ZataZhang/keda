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

## 隐私和外发

- 不写入私有 Issue 正文、客户代码、凭据或本机数据路径；prompt 仅作最小的问题/期望行为复述。
- 评审 payload 仍须经页面预览并逐次授权；本里程碑不改变评审授权流程。
