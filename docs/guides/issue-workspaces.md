# Issue 分支与工作区归属

GitHub Issue 编号只在所属仓库内唯一。KedaCode 使用完整 Issue URL 作为任务身份，
新分支与工作区按仓库隔离。例如，两个仓库的 #142 分别使用：

```text
kc/zatazhang/freshai/issue-142
kc/dewell-ai-assistant-team/freshai/issue-142
```

工作区位于 `.kc-worktrees/<完整分支名>`，如
`.kc-worktrees/kc/dewell-ai-assistant-team/freshai/issue-142`。
同一个 Issue 重试使用相同分支，不增加时间戳或随机后缀；不同 GitHub 服务实例也使用独立命名空间。
名称大小写会归一化，特殊目录名会编码，以兼容 Git、macOS 和 Windows。

启用独立数据库准备时，新任务的数据库标识也包含任务命名空间，避免同一检出目录中不同仓库的同号任务共用数据库。
已确认的旧分支沿用原数据库标识，不因命名升级迁移已有数据库。

创建完成后，KedaCode 在工作区的 `.agent-runner/issue-workspace.json` 保存完整 Issue URL 和分支，
并在 Git 公共目录的 `kedacode/issue-workspaces/` 保存续跑索引。
这些记录是本机运行状态，不进入实现提交。创建、查找、恢复发布和清理都会核对归属；
记录损坏或属于其他 Issue 时停止处理，不覆盖旧文件或重新指向旧提交。

## 配置兼容

推荐用 `{issue_branch}` 代表统一解析后的任务分支：

```toml
[agent_runner.worktree]
create_command = "kc worktree create --branch {issue_branch} --base-branch {base_branch}"
reuse_command = "kc worktree path --branch {issue_branch}"
path_command = "kc worktree path --branch {issue_branch}"
```

原有模板中的 `issue-{issue_number}` 会自动替换成统一解析的分支，已有配置不必手动迁移。
单独的 `{issue_number}` 仍表示数字，不改变自定义脚本所需的编号。
手动执行 `kc worktree create --branch <名字>` 时仍使用显式给定的分支名。

## 已有工作区

KedaCode 不会自动重命名、删除旧 `issue-<N>` 分支，也不会把其提交搬入新任务。
旧默认目录 `.kc-worktrees/issue-<N>` 中的归属记录与完整 Issue URL 相符，
或者该 Issue 明确引用的 PRD 头部 `GitHub Issue:` 链接完全匹配时，才继续使用旧分支。
PRD 可位于 pending 或 archive；其他仓库的同号链接不算归属证据。

没有归属证据的旧工作区会保留，新任务创建独立命名空间。自定义命令返回了无法确认归属的
旧目录时，KedaCode 会拒绝复用。需要人工接管时先检查 `git worktree list --porcelain`、
旧工作区的提交和未提交文件，确认完整仓库与任务来源；不要为了重试而删除旧目录。

`kc worktree cleanup --dry-run` 同时识别新分支与旧分支，但只处理当前仓库中归属可确认的任务。
关闭、远端分支已删除、工作区干净、提交已合并等原有清理条件仍然生效，`--force` 也不能绕过归属核对。

## 排障

查看任务日志中的完整分支后，用它定位工作区：

```bash
kc worktree path --branch kc/dewell-ai-assistant-team/freshai/issue-142
```

detached HEAD 自愈只作用于归属已确认的任务工作区。分叉历史仍需要人工判断，
不会通过 reset 或强制覆盖解决。修复根因后再按 [Agent Runner 指南](agent-runner.md)恢复任务。
