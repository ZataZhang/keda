# Issue #290 独立审查与验证

结论：PASS。实现由 Luna 执行器完成，本报告由主审独立检查代码、执行真实入口与定向测试后签核。需求源为 [Issue #290](https://github.com/ZataZhang/keda/issues/290)，没有关联 PRD。

## 代码树与验收范围

- 实现候选：`f53b4a0de54bedb10afca160a6b17de0b4a613c5`。
- 基线：`852217cc9269267974a513713c85f48b4b4bef0d`。
- `src` tree：`78014345ef10cc7c17fbb6bdca853242760f8aed`。
- 原实现 `tests` tree：`af9b97fb7752068eab80bc163eddf99dfb291e8f`。
- 机械拆分后的最终 `tests` tree：`7fa3bfa54c4f27193d125557b9d897301cfa8e26`。
- 收尾提交机械拆分测试、收敛测试参数并更新报告和验证清单；合并前必须确认最终生产与测试 tree 保持一致。
- 验收覆盖：缺少包管理器不阻断 Agent 启动、计划命令与诊断进入 prompt、依赖输入一致时优先复用、安装失败不伪报成功、权限与凭据问题可恢复阻塞、workspace 根/成员/排除及冲突证据被正确处理。

## 独立执行证据

| 命令或观察 | 结果 | 原始证据 |
| --- | --- | --- |
| `RV_KEEP_SCENES=1 bash tasks/evidence/issue-290/scripts/rv-1-real-entry.sh tasks/evidence/issue-290/rv-1-independent-final-rerun.txt` | 退出 0；7 个真实 CLI 正例、7 个负控全部通过 | `rv-1-independent-final-rerun.txt` |
| `uv run --group dev pytest --no-testmon tests/test_worktree_frontend.py tests/test_worktree_dependency_context.py tests/test_worktree_cli.py -q` | 105 passed，1.70 秒 | `rv-2-independent-pytest.txt` |
| `uv run --group dev pytest --no-testmon tests/test_agent_runner_orchestrate.py -q` | 57 passed，0.57 秒 | `rv-3-independent-orchestrate.txt` |
| 新 Python 进程独立读取所有 7 个正例的落盘报告、执行器探针和 GitHub 替身状态，并用真实 Git 检查复用链接未进入 status | PASS；缺工具未标 failed，权限/凭据为 blocked，复用链接有效且被忽略 | `rv-4-fresh-observation.json` |
| 测试机械拆分后对 frontend、package-manager、context、CLI、orchestrate 五个模块运行 `pytest --no-testmon` | 162 passed，2.05 秒；执行器执行，主审读取原始输出并确认搬移类 AST 完全一致 | `rv-6-post-split-pytest.txt` |
| 最终测试拆分与证据包暂存后，由主审运行无自定义目标覆盖的 `just test` | full lint 通过；104 passed，7.48 秒 | `rv-7-final-local-gate.txt` |

原始证据与 oracle 脚本发布在独立的 [证据分支](https://github.com/ZataZhang/keda/tree/iar-evidence/issue-290-manual-20261021/tasks/evidence/issue-290)，不把原始日志加入代码分支。该分支仅包含本轮选定文件，不沿用旧验收产物。

7 个负控分别恢复原始缺 npm 启动失败、错误的主机工具链前置要求、安装先于复用、跨目录 `*` 匹配、忽略 workspace 排除规则、断链误报就绪、遗漏无根 manifest 的 workspace。每个故障都在临时源码副本中注入，并通过相同 CLI/oracle 被拒绝；生产代码没有测试故障开关。

首轮脚本执行的 7 个负控已通过，但首个正例因 oracle 只认 `was not run`、不认实际诊断中的 `was never run` 而停止。主审检查落盘记录确认产品行为正确后，只修正脚本接受同义措辞，并增加 `exit_code is None` 与 PATH 确实缺 pnpm 的断言；完整重跑通过。原始首轮日志保留为 `rv-1-independent-final.txt`，未据此宣称产品通过，也未修改生产逻辑迁就脚本。

## 代码审查发现与处理

- BLOCKER（已解决）：旧分支会回退主线依赖输入匹配与所有既有软链接的忽略保护。最终实现保留 manifest/workspace/锁文件检查，并覆盖所有实际软链接。
- BLOCKER（已解决）：仅有 `pnpm-workspace.yaml` 的根目录漏扫描；pnpm workspace 与 npm 声明冲突时可能静默跳过成员。最终真实入口正例及对应负控已确认修复。
- BLOCKER（已解决）：新增凭据归因会把 `pnpm`、含 npm 字样的测试路径及成功命令警告当作 npm 失败。最终实现要求非零退出并按完整 argv token 识别工具，直接调用和 shell 包装的正反例均通过。
- BLOCKER（未解决）：0。
- NON-BLOCKING（未解决）：0。
- SECURITY（未解决）：0。审查了诊断脱敏、子进程 argv、提权/凭据边界及机器本地软链接处理，未发现本次新增安全问题。

测试文件超限已在交付前处理：包管理器测试整体移到独立文件，共享 helper 统一复用，原 frontend 测试降为 784 非空行。主审按 AST 独立确认搬移的测试类未改断言；参数化测试只把五列输入收敛成一个 tuple。生产 `src` tree 未变，因此真实 CLI 证据继续适用，最终测试 tree 则由拆分后的 162 个用例覆盖。

## 验证层级与限制

这是真实 `kc run --issue` 入口验证：生产 CLI、配置加载、runner 装配、worktree、依赖准备、子进程、prompt 与状态流均真实运行。GitHub 和模型执行器由隔离外部程序替代，模型行为由明确的计划与断言驱动；它证明上下文传递与 runner 对这些结果的处理，不证明真实模型必然自主作出相同选择。权限场景使用临时目录真实文件权限，凭据场景使用真实 npm 与本地 401 registry；没有修改真实 GitHub issue 作为测试。

实现方最终 `just lint --repo` 和 `uv run mkdocs build --strict` 均退出 0。显式变更文件的 jscpd/pylint duplicate-code 检查通过；干净树的复用检查跳过不能替代该显式检查。`just lint --repo` 内 `just test` 命中标记跳过，因此测试依据是上表实际执行的 162 个用例。较早的 4718 passed/1 skipped 属于最终分类补丁之前，不能单独证明最终代码树。

## 评审总结

🔴 严重：0；🟠 高：0；🟡 中：0；🔵 低：0。

远端实现候选 `f53b4a0d` 的 [CI](https://github.com/ZataZhang/keda/actions/runs/39542224258) 与 [Install smoke](https://github.com/ZataZhang/keda/actions/runs/39542224349) 通过；CI 实跑 4722 passed、1 skipped，294.40 秒。最终收尾提交仍由合并操作重新确认全部远端 checks，不跳过检查。

需求状态：✅ 通过。代码审查：✅ 完成。验证状态：✅ 本地、真实入口及实现候选 CI 通过。文档状态：✅ 已同步现有指南，无新增导航页。

```json
{
  "verdict": "approved",
  "summary": "独立审查及最终生产/测试树的真实 CLI、7 正例和 7 负控、162 个定向用例均通过；合并前仍须确认远端 checks 全部通过。",
  "findings": []
}
```
