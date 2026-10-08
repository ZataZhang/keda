# rv-2 执行器未报告模型时的诚实呈现

## 对应 oracle

PRD §1 行为样例：🤖 自动验证 |「执行器没有报告实际模型」→「请求模型可保留，
执行器报告模型显示『未提供』，不用配置值冒充」。

## 复现命令

与 rv-1 同一真实入口（隔离 `kc run` + fake_gh + 确定性 fixture 执行器）：

```bash
cd /Users/zata/code/keda/.iar-worktrees/issue-242
.venv/bin/python tasks/evidence/P1-FEAT-20261008-015223-agent-invocation-tracing-and-stall-diagnosis/scripts/rv_1_invocation_log.py
```

脚本自校验输出（`rv-1 PASS`）已包含对 `model_reported` / `model_source` 的
一致性断言；本文件摘取其中与 rv-2 相关的观察。

## 观察到的关键输出

场景 A（fixture 首个执行器 `claude` 自报了模型）：

```text
[iar-invocation-end] ... executor=claude outcome=error ... model_requested=claude-sonnet-4-5
model_reported=claude-sonnet-4-5-20250929 model_source=executor_report ...
```

场景 A/B 中未自报模型的调用（fixture 执行器 `kimi`）：

```text
[iar-invocation-end] ... executor=kimi outcome=ok ... model_requested=未下发
model_reported=未提供 model_source=unknown ...
```

- 执行器报了 → `model_source=executor_report`，`model_reported` 记执行器原话
  （注意与请求值 `claude-sonnet-4-5` 不同：报告值是执行器自己的版本串
  `claude-sonnet-4-5-20250929`，二者不混写）；
- 执行器没报 → `model_source=unknown`，`model_reported=未提供`；
  **没有**用配置里的模型名冒充报告值。

## 负控与预期失败

负控即 rv-1 场景 A 本身：若实现把「配置模型」回填为报告值，则 `claude` 失败
调用与 `kimi` 调用的 `model_source` 都会变成 `executor_report`，与上面
`unknown`/`未提供` 的观察相悖，rv-1 的调用清单断言（model_source 分布）失败。

## 已披露的边界（B1 折中，用户知情）

「已下发 model 旗标 + 执行器未报告」这一组合未单独构造：回退场景里模型绑定
按设计被丢弃（`model_requested=未下发`），因此样例覆盖了「请求+报告」与
「未请求+未报」两侧，「请求保留 + 报告缺失」的完整组合留待 rv-3 场景补充。
