# 内置任务恢复巡检

KedaCode 提供 `blocker-monitor` 模板，用于定期诊断 blocked / failed 任务并恢复可验证的技术故障。
安装、升级和 `kc init` 都不会自动注册它；`kc init` 完成后仅显示启用命令。
本功能使用现有 CLI 和 daemon，没有新增前端操作入口。

## 显式启用

先初始化并注册目标仓库，然后执行（将 `my-repo` 换成已注册仓库 ID）：

```bash
kc loop create my-repo-blockers --template blocker-monitor --repo-id my-repo
kc loop list
```

命令在目标仓库生成 `tasks/loops/my-repo-blockers.md` 并注册本机持久调度。
仓库、GitHub `owner/repo` 和 blocked / failed 标签从目标仓库的生效配置填入；
GitHub 身份依次使用注册表的 `github_repo`、仓库本地配置、GitHub remote，无法确定则拒绝注册。
模板要求显式传入已注册且启用的 `--repo-id`，不支持临时 `--repo`。
loop ID 在本机全局唯一，多仓库应使用不同 ID；每个仓库只启用一份恢复巡检。

**执行此命令即授权诊断和修复范围明确、可验证的技术阻塞，并在验证成功后恢复原任务。**
产品取舍、权限、密钥、受保护路径和外部审批继续交由维护者决定；代码修改遵循仓库规范并交付 Draft PR。
注册本身只写配方和调度状态，不启动 daemon、不立即创建 Issue、不调用 Agent，也不提交或推送文件。

保持目标仓库的普通 daemon 运行：

```bash
kc daemon --repo-id my-repo
```

已有后台 daemon 时无需重复启动。升级到包含该模板的版本后，重启旧 daemon 才能加载新代码。

## 运行行为与成本

- 默认每 30 分钟检查一次，可在注册时用 `--every 1h` 或 `--cron` 覆盖。
- 两类队列都为空时跳过，不创建 Issue、不调用 Agent；每轮仍会执行两次只读 GitHub 查询。
- 每类计数最多 1000，用于判断是否需要巡检；达到上限时 Agent 按配方分页读取任务。
- GitHub 读取失败时显示 `unknown`，在巡检正文保留最多 500 字符的单行错误诊断。
  此时创建诊断 Issue，不能把读取失败视为空队列或据此盲目重试原任务。
- 任一上一轮 `loop/<id>` Issue 未关闭时暂停下一轮，包括等待 PR 合并或人工决定。
  维护者须处理该 Issue 后关闭或合并对应 PR，调度才会继续。
- daemon 停止或机器关机期间不执行；配置持久保存，恢复运行后按现有 loop 规则补发一次。

预览一次触发：

```bash
kc loop run my-repo-blockers --dry-run
```

预览会执行只读计数命令，不创建 GitHub Issue、不调用 Agent、不推进调度。

## 修改与停用

生成的配方由仓库维护者管理，升级不会自动覆盖。即使传入 `--force`，模板创建也不会覆盖已有文件。
修改配方后按普通 recipe 重新注册；`--force` 仅允许替换调度条目：

```bash
kc loop create my-repo-blockers --recipe tasks/loops/my-repo-blockers.md --force
kc loop cancel my-repo-blockers
```

停用只删除本机调度，不关闭已生成的 Issue、不终止已经运行的任务，也不删除配方。
原有自定义巡检不会自动迁移；使用前检查 `kc loop list`，避免同一仓库注册两份同类巡检。
