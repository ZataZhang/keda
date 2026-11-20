## 人审导航 / Human Review Navigation

| 人审内容 | 呈递物与打开方式 | 逐项期望值 |
|---|---|---|
| rv-1：仓库声明 `gitea` 后，代码平台命令是否在访问前停止并说明原因与改法 | 文件：`/Users/zata/code/keda/.kc-worktrees/issue-398/tasks/evidence/P2-FEAT-20261119-152718-repository-platform-selection-skeleton/rv-1-gitea-not-implemented.txt`。打开：`open "/Users/zata/code/keda/.kc-worktrees/issue-398/tasks/evidence/P2-FEAT-20261119-152718-repository-platform-selection-skeleton/rv-1-gitea-not-implemented.txt"`。 | 退出码为 `1`；输出包含 `gitea`、`platform`、`not implemented`；说明当前仅支持 `github` 并指出如何改回；输出不含 `Traceback`。 |

关键输出摘录（保留终端的实际自动折行）：

```text
Error fetching <repo>:
/private/var/folders/n8/x_smwvn16b3b30f7wnl4s2kh0000gn/T/tmp.To7Kt7yrd2: The
repository platform 'gitea' is not implemented. Only 'github' is currently
supported. To switch back to GitHub, edit
/private/var/folders/n8/x_smwvn16b3b30f7wnl4s2kh0000gn/T/tmp.To7Kt7yrd2/.kedacod
e.toml and set platform = 'github'.
exit=1
acceptance_assertion=PASS
```

该行来自真实 `uv run kc --repo <temporary repository> issue list` 输出，证据文件完整保留 Rich CLI 的原始换行。人审期望：退出码 `1`；包含 `gitea`、`platform`、`not implemented`、当前仅支持 `github` 和回退方法；不含 `Traceback`。关联：[GitHub Issue #398](https://github.com/ZataZhang/keda/issues/398)。本执行流未创建 PR 或 CI 运行，因此没有对应链接；由 runner 后续发布。

## 验证结果

| RV | 结果 | 关键证据 |
|---|---|---|
| rv-1 | PASS | gitea 真实 CLI 入口退出码 1，提示含实现状态和回退方式，无堆栈；github 负向对照不含该提示。 |
| rv-2 | PASS | 变更前真实 CLI 接受 `gitlab`（退出码 0）；当前真实 CLI 在加载配置时退出码 1，并列出 `github` / `gitea`。 |
| rv-3 | PASS | 未声明平台与显式 `github` 的真实入口均以 1 结束，平台/配置错误提示数为 0；gitea 负向对照触发未实现提示。 |
| rv-4 | PASS | gitea 配置下的本地生命周期查询退出码 0；同一配置的 Issue 查询退出码 1。 |
| rv-5 | PASS | 定向 pytest 实际运行并通过。`CI=1 PYTEST_ADDOPTS=--testmon-noselect just test` 完整执行本地测试目标：`5608 passed, 1 skipped`。架构搜索显示客户端只在工厂构造、`gitea` 只定义于共享模型、工厂调用数为 26 且与基线一致、签名未变、接口 diff 为空。 |
| rv-6 | PASS | `uv run mkdocs build --strict` 退出码 0；配置说明与 Agent Runner 交叉引用搜索均命中。 |

运行 `UV_CACHE_DIR=/private/tmp/keda-issue-398-uv-cache uv run kc evidence check --evidence tasks/evidence/P2-FEAT-20261119-152718-repository-platform-selection-skeleton` 结果为 `6/6 passed`；本地门禁复跑超时覆盖为 1260 秒，复跑缓存已关闭。RV-5 的 stdout 断言检查 pytest 失败摘要（` failed,` / ` failed in`），不把测试名中的示例文本“evidence check failed”误判为失败用例。早先的全量诊断运行曾分别在 backlog API 与 max-file-lines guard 用例失败；两个用例各自单独运行时通过，随后完整全量运行通过。没有截图、录屏或界面变更；此 PRD 的人审面是 CLI 输出。

## 验证身份

- 工作分支：`issue-398`
- 验证时 HEAD：`f5cffa14ffb979a74d9a8b622b78136dd45bc0cf`
- 验证时 `HEAD^{tree}`：`e0b1f2355668b302741b62ebf1233f89cd4518af`
- 实现已在工作树 HEAD 中。PRD 归档和代码落地由 runner 处理；本次没有创建 PR。

## 复核备注

执行者检查已完成，独立 verifier 尚未在此执行环境中运行；本报告不把执行者自检称为独立 verifier PASS。Runner 应在发布流程中执行独立复核并据其结果处理归档。
