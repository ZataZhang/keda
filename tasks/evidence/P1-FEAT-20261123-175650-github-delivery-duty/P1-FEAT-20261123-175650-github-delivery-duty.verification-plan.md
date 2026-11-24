# 验证计划：交付值班员

## 目标与边界

按 PRD §7.6 的四个 Realistic Validation 项逐项验证。手动入口只读；巡检写动作只在隔离仓库和专用 runner 上进行；历史评测需冻结来源并由不知分组的评分者对照。不得对真实 #419 或其他业务 Issue 重放巡检写操作。

## RV-1：真实手动入口与只读边界

- 正向：刷新随包技能，在新 Codex 会话分别从真实 Issue 与其真实关联 PR 入口提问；记录观察时点、状态、证据、责任人 / 动作 / 复查条件。
- 新进程在查询前后读取 Issue 和 PR timeline，比较事件数和最后事件；保存当前 PR head 及 checks 对应 SHA，并记录会话写调用数。
- 负控：使用未安装 `duty.md` 的技能版本，对旧失败记录与新 head 进行中检查并存的现场重复查询；记录是否建议立即重试或发出写调用。
- 主演示：按 9.1 的五个步骤录屏，并让每个结果画面停留至少三秒。
- 当前结果：Codex 在进入验证前因 `workspace routing discovery timed out` 退出；未执行正向、负控或录屏。详见 `rv-1-duty-read.md`，分类为 `REVIEW_INCIDENT / INCONCLUSIVE`。

## RV-2：隔离巡检

- 在隔离测试仓库执行 `kc loop create <loop-id> --template blocker-monitor --repo-id <test-repo>`，由专用 runner 的 daemon 按排班触发一轮。
- 准备一个可恢复目标和一个仍挂失败标签但已有明确人工接管的目标；使用改动前配方作负控。
- 巡检后由新进程重读两个目标的 timeline、日志及巡检 Issue 正文，核对授权动作、人工接管目标零写入和摘要幂等。
- 当前没有隔离仓库和专用 runner，因此未注册、未调度、未执行负控；详见 `rv-2-duty-patrol.txt`。

## RV-3：冻结历史现场的盲评

- 先核实四个现场来源与观察时点，屏蔽之后信息，形成 `cases/`；每题建立独立参考判断。
- 固定模型、预算、提示可见材料，以普通提示和已安装岗位技能分别处理；评分者不知道分组。
- 逐题报告漏报 / 应接手数、误报 / 提醒数和可得耗时；分母为零标为“不适用”。岗位组有越权、虚构证据或漏掉接管时先修正岗位说明再重跑。
- 负控为普通提示组。当前未获得四题冻结材料，未盲评，见 `rv-3-replay.md`。

## RV-4：分发、配方与文档

- 负控脚本：`sh tasks/evidence/P1-FEAT-20261123-175650-github-delivery-duty/scripts/rv-4-negative-control.sh`。它临时移除模板 recipe 对 `references/duty.md` 的引用，要求模板安装测试变红，并用 trap 恢复源文件。
- 正向命令（worktree 根目录）：

  ```sh
  UV_CACHE_DIR="$PWD/tasks/evidence/P1-FEAT-20261123-175650-github-delivery-duty/scripts/.uv-cache" uv run pytest -o addopts='' tests/test_loop_builtin.py tests/test_keda_blocker_monitor_recipe.py tests/test_cli_skill_install.py tests/test_kedacode_operator_skill.py && UV_CACHE_DIR="$PWD/tasks/evidence/P1-FEAT-20261123-175650-github-delivery-duty/scripts/.uv-cache" uv run mkdocs build --strict
  ```

- 正向结果：54 个目标测试通过；严格文档构建退出码 0。完整命令输出在 `rv-4-install-docs.txt`，负控失败输出在 `rv-4-negative-control.txt`。
- `rv-4-architecture-audit.sh` 核对路由 / recipe 引用、references 数量和 PRD 变更树；`rv-4-tree-identity.sh` 将基准 HEAD 与最终实现 / 测试文件摘要保存到 `rv-4-tree-identity.txt`。最终提交 SHA 要由 runner 提交后再补充核对。
- 完整 `just test` 曾运行 481.44 秒并暴露 references 数量仍为 12 的三条断言；已修正数量 / 路由表契约并由目标套件验证。该完整命令超过默认 300 秒复跑上限，本轮未再执行，因此最终树的 `just test` 验收仍未完成。
- `just lint --reuse` 通过；`SKIP=check-test-flag just lint --full` 在最终代码上复跑通过。首次 full lint 的 Ruff formatter 自动改写了一个文件，第二次复跑无自动改写并通过。UV 缓存定向到证据脚本目录，避免访问受限的全局缓存。

## 未完成的人工 / 外部验证

独立 verifier 未运行；真实 Codex 会话、隔离巡检 runner、盲评和主演示尚未完成。所有未完成项保持开放，不能用本地测试结果替代。
