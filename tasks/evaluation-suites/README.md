# 评测集（Evaluation Suites）维护指南

本目录是 Console 模型评测使用的版本化 suite registry。每个 YAML 文件定义一批固定样本；可选资料放在同级 `<yaml 文件名去掉后缀>.d/<case_id>/`。run 冻结 suite 内容摘要、每题基线、执行预设与重复次数，后续文件变化不影响已有 run。

## 目录与兼容性

- Registry 只扫描 `*.yaml`；`example.yaml` 是旧格式样例，保持可直接运行。
- Suite `id` 在本目录唯一。内容变化需提升 `version`。
- 没有逐题 `base_ref`、`split`、判分资料等新字段的 v1 suite 继续按旧语义运行：每题沿用顶层 `base_ref`、重复一次且不要求校准。
- v2 suite 的 YAML 及其中声明的 `held_out` 与 `gold_patch` 内容参与 `content_hash`。
  这些内容变化后需要重新校准。本地生成的 Python 字节码和目录缓存不影响校准。

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

候选使用导出的基线文件创建全新 Git 仓库，仓库只有一个提交且不共享来源仓库对象库。创建快照时先从临时 Git index 中剔除 `held_out` 与 `candidate_hidden` 路径，再导出文件和提交，因此这些路径不会进入候选工作树或候选提交；Agent 无法通过 `git show HEAD:<path>` 或 `git checkout -- .` 还原它们。候选退出后，harness 在收集代码 diff 前先丢弃这些路径上的候选内容；随后从冻结来源提交还原基线文件、覆盖留出的判分材料并执行检查。隐藏材料不会进入评审 diff。`@noop` 不改源码，`@gold` 应用原始非测试修复；二者都不调用模型。

声明的 `candidate_hidden` 路径在候选仓库创建前从冻结基线 index 剔除；候选完成后，harness 丢弃这些路径上的候选改动并恢复冻结基线版本供判分。`held_out` 路径同样不会写入候选仓库，但仍可在判分前恢复并覆盖独立测试材料。这里的“不可见”限于候选工作树及其 Git 历史；该字段仅处理已确认存在于基线中的答案材料，不提供通用文件系统沙箱，也不保证执行器无法读取宿主机上的其他目录。

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

`keda-history-v1.yaml` 的 12 题（8 dev + 4 holdout）冻结自 `keda-datasets` 数据版本 `c9cb2672364d4db26580498b490bf79e6d28e0b9`（`manifest_sha256` = `2a6585a1eeb3d9e273d133b76fa9e218d70df20166226caf50bb4577b768e5b0`）。初版 1.0.0 的 YAML 摘要为 `48792e3aecf3cae29dbacb77186634cbe084b867bd822c1ceb2a08d41050191e`，Console 内容摘要为 `5f78f39cd2d604f79b3c98c00e68025312446c2967ea3237be1c1c3fb3cdfbea`。下表与 `tasks/evidence/P1-FEAT-20261019-140531-m13-golden-eval-baseline/rv-2-seed-selection.json` 同源（更细的 source 文件摘要、gold patch 路径与逐题资料清单见该 JSON 与 suite YAML）。`base → final` 是冻结修复的提交范围，用于核对 gold patch 来源。Console 按当前版本单独进行三次 `@noop` / `@gold` 校准。

1.1.0 保留各题的冻结基线和参考补丁。修订了 CI 判断、进程捕获、日志回退、短期记忆、失败交接、PR 文案和 Stats 接口七题。判分副本移到独立的 `tests/test_eval_*.py` 路径，让候选保留并可运行基线中已有的回归测试。日志回退使用真实 CLI 配置和当前日期，Stats HTTP 用例使用窗口内的记录，进程捕获补验后代持有管道时的实际等待上限。旧版实跑和新版按各自的判分材料保留。

1.2.0 明确 GitHub 用量账本在直接调用记录方法时也应降级。判分把账本路径占为目录，制造真实的写入失败，再核对原有 GitHub 操作结果和跨进程用量统计降级告警。该题的基线测试保留给候选。

1.3.0 使用真实 Git 工作区调用 Draft PR 发布入口，以接收的标题和正文判分。GitHub API 与生成文本使用测试替身。候选可以在生成或发布阶段满足同一外部契约。

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

## 扩展 suite `keda-history-expanded-v1`

扩展集从公开 Issue 提炼行为要求，以对应合并提交的第一父提交为基线。判分目标由公开合并版本中的行为测试筛选或改写，每道题运行三次基线负控与原修复正控。证据自检题通过安装后的 `kc` 执行本地命令，测试进程的配置和 Git 仓库均隔离。

1.1.0 明确讨论失败隔离题的默认容错、严格模式和结果契约，判分使用独立文件。Issue 输出时间戳的判分调用真实子进程，通过管道接收输出后核对日志和 Console 回传的时间。终端传输作为独立回归检查。

1.2.0 的仓库初始化题通过已安装的 `iar` 和实际 Git 仓库验证拦截及初始化后可用性。终端题在伪终端验证裸命令和具名 `iar repl`，模型 CLI 以本地测试程序替代。结构化证据题明确配置语言和关闭开关，走 PRD 发布入口核对生成的 Issue 正文。

1.3.0 明确日志续读的请求参数、响应字段及错误状态。CLI 默认显示尾部，Console HTTP 请求以 `tail=true` 读取尾部。rebase 题使用真实 Git 冲突和本地远端，核对正确分支能够恢复，以及分支证据不符或缺失时拒绝继续。两题均保留基线测试。

1.4.0 用安装后的 `iar deliberate`、真实 Git 仓库和本地脚本执行器判分。四种情形覆盖参与者失败后回退、严格模式、全部失败和仅综合失败。判分核对回退前后的输出、`session.json` 和退出码。参考补丁在历史修复上补齐输出保留和部分成功退出码，基线与题面保持原样。

1.5.0 修正 Issue 日志题的空态判分。没有该 Issue 的日志时，接受静默成功或空态提示，并用另一 Issue 的真实日志验证输出隔离。基线、题面和参考补丁沿用 1.4.0。

1.6.0 使用 HTTP 端点实际返回的尝试标识验证旧尝试续读、尝试消失及文件截断。判分要求标识可稳定回传，接受包含或省略扩展名的表示。

1.7.0 将 Skill 刷新改为隔离 HOME、实际 Git 和安装后的 CLI 检查，不要求安装函数位于
某个内部模块，也不固定 init 引导文本的 stdout/stderr 通道。证据自检在已初始化的 Git
仓库执行，按命令帮助选择公开的证据目录选项，验证退出码、每项结果、超时继续和无效清单
零执行，不固定题目未声明的 JSON 字段布局。原有四项交付门禁聚合检查保留，并把判分副本
移到独立文件，让候选继续看到基线回归测试。旧版成绩保留，新版对双方旧补丁统一重放。

1.8.0 修正聚合门禁检查对 `Item` / `item`、`Timed out` / `timed out` 的文案耦合。
仍检查每条失败命令与失败内容、超时标识和后续执行；不要求固定汇总句式。门禁调用用例
使用进程替身，同题 8 项安装后 CLI 用例覆盖真实子进程执行。新版继续统一回放双方旧补丁。

| 题目 | 公开 Issue | 合并 PR |
| --- | --- | --- |
| `deliberation-failure-isolation` | [#102](https://github.com/ZataZhang/keda/issues/102) | [#106](https://github.com/ZataZhang/keda/pull/106) |
| `tty-repl-entry` | [#103](https://github.com/ZataZhang/keda/issues/103) | [#109](https://github.com/ZataZhang/keda/pull/109) |
| `repository-init-cli-gate` | [#82](https://github.com/ZataZhang/keda/issues/82) | [#83](https://github.com/ZataZhang/keda/pull/83) |
| `structured-evidence-opt-in` | [#85](https://github.com/ZataZhang/keda/issues/85) | [#88](https://github.com/ZataZhang/keda/pull/88) |
| `issue-log-resume-and-isolation` | [#157](https://github.com/ZataZhang/keda/issues/157) | [#163](https://github.com/ZataZhang/keda/pull/163) |
| `issue-output-event-timestamps` | [#223](https://github.com/ZataZhang/keda/issues/223) | [#236](https://github.com/ZataZhang/keda/pull/236) |
| `standalone-operator-skill-refresh` | [#245](https://github.com/ZataZhang/keda/issues/245) | [#253](https://github.com/ZataZhang/keda/pull/253) |
| `detached-rebase-recovery` | [#73](https://github.com/ZataZhang/keda/issues/73) | [#75](https://github.com/ZataZhang/keda/pull/75) |
| `validate-prd-issue-anchor` | [#87](https://github.com/ZataZhang/keda/issues/87) | [#90](https://github.com/ZataZhang/keda/pull/90) |
| `evidence-check-cli` | [#341](https://github.com/ZataZhang/keda/issues/341) | [#342](https://github.com/ZataZhang/keda/pull/342) |

## 独立行为 suite `keda-history-recovered-v1`

前两个归档样本的旧测试依赖新增的内部符号。新测试调用冻结基线已有的入口，分别用真实子进程和本地 Git 远端验证行为。1.1.0 补入 Console 实例复用，使用已安装的 CLI 和本机 HTTP 服务验证进程的启动参数、探测重试与复用。基线测试保留在候选工作区，判分文件另行冻结。

| 题目 | 公开 Issue | 合并 PR |
| --- | --- | --- |
| `process-error-detail` | [#72](https://github.com/ZataZhang/keda/issues/72) | [#76](https://github.com/ZataZhang/keda/pull/76) |
| `worktree-remote-alignment` | [#71](https://github.com/ZataZhang/keda/issues/71) | [#77](https://github.com/ZataZhang/keda/pull/77) |
| `console-existing-instance` | [#250](https://github.com/ZataZhang/keda/issues/250) | [#252](https://github.com/ZataZhang/keda/pull/252) |

## 新行为 suite `keda-behavior-v2`

新增 10 道来源不同的任务候选，覆盖初始化忽略规则、监控读失败与历史去重、协议取消、
内容生成时限、一次性执行器覆盖、远端失败摘要、启动 Skill 同步、状态板丢失响应和
Console 端口复用。每题参考修复与来源提交在 `.d/provenance.json` 留档。
判分优先使用实际 Git、SQLite、HTTP、socket 和子进程；来源仍限于 Keda 历史。
本批描述为 Agent 提炼，未经人工逐题审核，使用后归开发集，不作为未接触留出集。

1.1.0 为历史去重增加 1,201 次重复记录和窗口边界检查；澄清一次性执行器选择题：
显式 CLI 选择仍最高，重复请求的新选择替换待消费旧选择。该题需要三组重新实跑，
旧补丁回放只作诊断。

1.2.0 补查带地址复用的通配监听冲突。macOS 上原公开参考补丁也会漏报这个冲突，
因此参考修复同时补入有时限的 TCP 探测；来源提交与后续修正分别记录，保留原版。
此边界来自真实审查发现并经独立复现，各组候选统一回放，属于后验诊断。
版本修订、重复次数和新增断言均不增加独立任务数。

1.2.1 修正发布时空白整理造成的参考补丁 hunk 行数失配，并保留已格式化的测试。
任务要求与判分行为不变；内容摘要变化后重新校准，旧校准与回放记录保持原样。

运行前必须完成各题三次行为负控和三次正控。三组对照入口与结论边界见
[`docs/dev/evaluation-ablation.md`](../../docs/dev/evaluation-ablation.md)。

## 隐私和外发

- 不写入私有 Issue 正文、客户代码、凭据或本机数据路径；prompt 仅作最小的问题/期望行为复述。
- 评审 payload 仍须经页面预览并逐次授权；本里程碑不改变评审授权流程。
