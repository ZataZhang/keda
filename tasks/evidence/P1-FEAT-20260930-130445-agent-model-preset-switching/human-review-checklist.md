# Human Review Checklist — P1-FEAT-20260930-130445-agent-model-preset-switching

> 这是把 PRD §9.2 里仍**未勾的 5 项 Human-Confirmed** + 1 项**待你拍板的选择**汇总成一页的人审清单。你只需读这一份，不必翻 PRD、证据目录或对话记录。
> 打开方式：`just prd review tasks/archive/P1-FEAT-20260930-130445-agent-model-preset-switching.md`（有交互版 `human-review-checklist.html` 时优先打开它）。
>
> 交付侧状态：两轮独立 verifier 均 **PASS**；`CI=true just test all` = **2849 passed / 1 skipped**；验证绑定代码树（record 路径排除口径）`9ff0eb01`。
> 术语：**预设（preset）** = 一组 `(agent, 模型, 推理档)`；**绑定（binding）** = 把某个生命周期阶段指到一个预设；**argv** = keda 实际拉起 agent CLI 时拼出的命令行参数数组。

**怎么回复**：对每一项给一个结论即可——`同意` / `选 <选项>` / `有差异：<说明>`。全部同意即视为人审通过。

---

## 审阅顺序总表

| # | 你要决定的事 | 判错的后果 | 结论 |
|---|---|---|---|
| 1 | 模型注入只走 core 单一 argv 出口，且"未启用即逐字节不变"可作为硬承诺 | 若别处也拼模型参数，会双注入/漏注入，且"零回归"承诺失真 | ☐ 同意 ☐ 有差异 |
| 2 | 阶段绑定预设整体决定 agent+模型，遮蔽矩阵同键声明，显式 `--agent` 最高 | 优先级搞反会导致"我明明绑了却没用"或"临时覆盖压不住配置" | ☐ 同意 ☐ 有差异 |
| 3 | 换到不同 CLI 的 agent 时必须丢弃模型绑定并记日志；`fix`/`closeout` 跟随实现者继承不算换人 | 把 A CLI 的模型参数塞给 B CLI 会直接报错或行为错乱 | ☐ 同意 ☐ 有差异 |
| 4 | agent 没声明模型模板时宁可 fail-fast，也不静默忽略 | 静默忽略会让你以为模型生效了、实际没生效 | ☐ 同意 ☐ 有差异 |
| 5 | §9.1 三个可见结果已逐项过目 | —— | ☐ 同意 ☐ 有差异 |
| 6 | **选择**：rv-12（真实跑一轮，opt-in）接受披露的缺口，还是要求补采 | 补采消耗模型额度；不补则"绑定生效时端到端跑通"未被真实执行验证过 | ☐ 6a 接受缺口 ☐ 6b 要求补采 |

---

## 1. 决策一 · 单一 argv 出口 + 零回归

**你要决定**：把"模型参数只在一个地方注入、且不启用任何预设时命令行与以前完全一样"当作硬承诺接受下来。判错的话，别处再拼一次模型参数就会双注入，"零回归"也变成空话。

> **PRD 原文（§9.2 Human-Confirmed item 1）**：
> 决策一：预设解析与 argv 模型注入落在 core 单一出口、零回归为硬承诺 —— 人确认（`rv-1`、`rv-4` 为佐证）

**大白话**：全仓库只有 `agent_invocation.build_agent_invocation` 负责把 `--model` / 推理档塞进命令行；你没设预设、没绑阶段、没传旗标时，拉起来的命令行和历史版本一字不差。

**证据（关键行逐字摘录）**：
- 启用时确实注入了（`rv-1-agent-doctor-preset.txt`）：
  `"--model", "glm-5.3-flash", "--settings", "{\"reasoningEffort\":\"max\"}"`，且条目带 `"preset": "plan"`。
- 未启用时无任何模型参数（`rv-4-agent-doctor-claude.txt`，claude argv 里没有 `--model`）。
- 34 条黄金快照 `tests/test_agent_invocation_golden.py` 相对 `main` **零 diff**、全绿——"逐字节不变"是直接证据。
- 架构 rg 门（`architecture-rg-gates.txt`）：`argv.append/argv.extend` 在 core/engines 的全部命中只有组装器本体 `agent_invocation.py` 与既有的 `container_ops.py`（容器日志旗标，未改动）。

**可选复跑**（不跑也能判）：
```bash
uv run pytest -o addopts='' tests/test_agent_invocation_golden.py -q
uv run iar agent doctor claude --json      # 期望：argv 无 --model/--settings
```

**结论**：☐ 同意 ☐ 有差异：__________

---

## 2. 决策二 · 绑定整体决定 + 遮蔽矩阵 + 显式 `--agent` 最高

**你要决定**：接受"绑了预设就以预设为准"的优先级——预设同时接管该阶段的 agent 与模型，且压过矩阵里同键的 agent 声明；命令行显式 `--agent` 仍然是最高优先级。判错的话，要么"绑了不生效"，要么"临时覆盖压不住配置"。

> **PRD 原文（§9.2 Human-Confirmed item 2）**：
> 决策二：阶段绑定预设整体决定 (agent, 模型, 推理档)、遮蔽矩阵同键声明、CLI 显式 `--agent` 最高 —— 人确认（`rv-6`、`rv-9` 为佐证）

**大白话**：`[agent_runner.lifecycle_presets]` 或 PRD 头部的 `lifecycle_presets` 块把某个阶段（如 `verifier`）绑到预设后，这个阶段的 agent **和**模型都由预设决定；矩阵里同键的旧声明被压住；删除绑定即恢复矩阵语义。

**证据（关键行逐字摘录）**：
- 绑定生效（`rv-9-agent-doctor-lifecycle.txt` 上半，`IAR_CONFIG` 里 `verifier = plan`）：
  `verifier · codebuddy`，argv 含 `--model glm-5.3-flash`、`--settings '{"reasoningEffort":"max"}'`，`preset: plan`（exit 0）。
- 解绑回落（同文件下半，真实 config，矩阵 `verifier=qoder`）：`verifier · qoder`，argv `qodercn ...` 无任何模型参数（exit 0）。
- 单测覆盖遮蔽与优先级：`tests/test_agent_model_presets.py`（21 passed），含"绑定遮蔽矩阵同键"、"显式 `--agent` 最高"。

**可选复跑**：
```bash
uv run pytest -o addopts='' tests/test_agent_model_presets.py -q
```

**结论**：☐ 同意 ☐ 有差异：__________

---

## 3. 决策三 · 换人即丢弃模型绑定；executor 继承不算换人

**你要决定**：接受"一旦实际执行 agent 与预设声明的 agent 不同，就丢弃模型/推理档参数并记日志"的安全策略，同时"`fix`/`closeout` 跟随实现者"（同一 agent）视为继承、不算换人。判错的话，把 A CLI 的模型参数喂给 B CLI 会报错或行为异常。

> **PRD 原文（§9.2 Human-Confirmed item 3）**：
> 决策三：换人（fallback / 显式 `--agent`）即丢弃模型绑定并标注，executor 继承不算换人 —— 人确认（`rv-7`、`rv-10` 为佐证）

**大白话**：不同 agent CLI 的模型命名空间不同（codebuddy 的 `--settings` 对 claude 没意义），所以换人时必须丢参数；但"还是同一个 agent、只是从实现阶段轮到修复/收尾阶段"时，绑定继续用。

**证据**：
- `drop_model_selection_for_agent`（`run_agent_once.py`）：agent 不一致 → 记 WARN `model binding dropped on agent switch` 并返回 `None`；一致 → 原样透传。单测 `test_drop_model_selection_on_agent_switch`。
- `fix`/`closeout` 未自绑时继承实现者绑定，由 resilient 层统一兜底丢弃。
- verifier 候选回退也走同一判定（每个候选按自身 agent 丢弃绑定）。

**可选复跑**：
```bash
uv run pytest -o addopts='' tests/test_agent_model_presets.py -q -k 'drop or fallback or executor'
```

**结论**：☐ 同意 ☐ 有差异：__________

---

## 4. 决策四 · 模板缺失 fail-fast

**你要决定**：接受"agent 没有声明模型参数模板时，用带模型的预设就指名报错、不静默忽略"。判错的话，你会以为模型生效了、实际没生效。

> **PRD 原文（§9.2 Human-Confirmed item 4）**：
> 决策四：agent 未声明模型模板时 fail-fast（宁缺勿假）—— 人确认（`rv-5` 为佐证）

**大白话**：只有已核实命令行语法的 agent（codebuddy、claude）播种了模板；给别的 agent（如 kimi）带模型的预设时直接报错，错误信息点名是哪个 agent、缺什么、怎么补。

**证据（关键行逐字摘录，`rv-9-agent-doctor-lifecycle.txt` 末段）**：
```
doctor failed: agent 'kimi' has no model_args template, so the model binding
(model='glm-5.3-flash') cannot be applied. Declare [agent_runner.agents.kimi].model_args
(e.g. ["--model", "{model}"]) in config.toml / .iar.toml.
exit(kimi+plan)=1
```
- 单测 `test_build_agent_invocation_fails_fast_without_template`、`..._effort_only_binding_requires_effort_template`。

**结论**：☐ 同意 ☐ 有差异：__________

---

## 5. §9.1 呈递区已过目

**你要决定**：确认下面三个可见结果你已经看到了、且与预期一致。

> **PRD 原文（§9.2 Human-Confirmed item 5）**：
> 9.1 呈递区呈递物已逐项过目并认可

**证据（相对本文件所在目录）**：
- `rv-1-agent-doctor-preset.txt` — 启用预设时 argv 含模型/推理档 + `preset` 字段。
- `rv-9-agent-doctor-lifecycle.txt` — 绑定生效 vs 解绑回落 vs 模板缺失 fail-fast 三段对照。
- `rv-4-agent-doctor-claude.txt` — 未启用时 argv 零模型参数。

**可选复跑**：
```bash
uv run iar agent doctor codebuddy --json --preset plan
uv run iar agent presets
```

**结论**：☐ 同意 ☐ 有差异：__________

---

## 6. 选择 · rv-12（opt-in）缺口怎么处理

**你要决定**：rv-12「绑定生效时真实跑一轮 runner」需要真实 Issue + 真 agent 子进程，会消耗模型额度，属 opt-in。当前版本**未采**，只由账本写入的单测间接覆盖。你是接受这个已披露的缺口，还是要求补采？

**两条路**：
- [ ] **6a. 接受披露的缺口**（推荐，成本最低）：端到端"绑定 → 真实 runner 跑通"未被真实执行验证过，但账本列读写、argv 注入、绑定解析均由单测与真实 CLI 入口（doctor）证明。
- [ ] **6b. 要求补采**：需要真实 GitHub Issue 与模型额度，补跑后再回来勾选；会延后合并。

**参考**：`P1-FEAT-20260930-130445-agent-model-preset-switching.evidence-report.md` 的「已知限制」第 1 条；PRD §12 Follow-up。

**结论**：☐ 6a 接受缺口 ☐ 6b 要求补采

---

## 附：两条 LOW 级、非阻塞的已知项（知情即可，不需要你决策）

- **verifier R-02（LOW）**：`closeout`/`fix` 的用量**观测**回调在重解析 agent 时未透传 `prd_preset_overrides`——纯观测归因，不影响 argv 与实际执行 agent；留作后续。
- **verifier R-03（LOW）**：rv-1 声明的负控/冷启动探针未单独留痕，实质已由 rv-5 与单测覆盖。
