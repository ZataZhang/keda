# 从 IAR 升级到 KedaCode（`kc`）

当前命令入口是 `kc`（也可使用 `kedacode`）。旧 `iar` 命令、`IAR_*` 环境变量、`~/.iar` 状态目录、
`.iar.toml` 仓库配置、旧 shell 补全变量和旧插件入口点不再由运行时自动识别。PyPI 包名
`kedacode` 与 GitHub 仓库名 `keda` 保持不变。

## 更新命令、变量和插件

| 旧名称 | 当前名称 |
|---|---|
| `iar <command>` | `kc <command>`（也可用 `kedacode <command>`） |
| `IAR_<SUFFIX>` | `KEDACODE_<SUFFIX>` |
| `iar-operator` | `kedacode-operator` |
| `iar.agent_output_protocols` | `kedacode.agent_output_protocols` |
| `_IAR_COMPLETE` | `_KC_COMPLETE`（重新安装 shell 补全） |

把 shell 配置、CI、脚本和容器配置中的环境变量改为 `KEDACODE_*`，并重新安装补全：

```bash
kc completion install
```

## 协作标记升级顺序

新版 runner 只识别 `kc:` 协作标记和 `<<KC_EXEC>>` 会话命令块，不识别旧 `iar:` / `IAR_EXEC` 写法。
未迁移的在途 Issue 会被当成没有旧认领或依赖标记。新旧 runner 不能混跑，因此每个仓库都按下面顺序
升级：

1. 在**所有**运行 KedaCode 的电脑上停止 daemon、container 和正在执行的 `kc run`。
2. 安装新版 KedaCode。
3. 在仓库目录预演迁移；确认 Issue / PR 清单和预计编辑次数。
4. 预演无误后显式执行 `--apply`。
5. 将各目标仓库的 `.github/workflows/validation-gate.yml` 同步到当前版本，确认它解析
   `kc:realistic-validation` 清单；旧工作流只识别 `iar:`，会把新清单当成不存在。
   反过来，新版工作流不再放过残留清单：正文里出现读不懂的签收区块（旧前缀或残缺
   marker）时 check 直接失败，因此必须在本机停跑 runner 后执行第 4 步再合并。
6. 重新启动 runner。

```bash
kc config migrate
kc config migrate --apply
```

默认只扫描 open Issue 与 PR 的正文和全部评论。迁移只替换标记前缀，其他文字保持不变；可以重复运行，
第二次不会再次改写已迁移的标记。closed Issue / PR 默认跳过，需要时加 `--include-closed`。`--apply`
会在本机检测到 runner 正在运行时拒绝执行；它无法检测其他电脑上的进程，必须先完成所有机器停机。
占用检查同时覆盖新旧状态目录中的受管进程登记与锁；旧目录已移除时，新目录中的活动记录仍会阻止执行。

如果跳过迁移，在途 Issue 的旧认领会被忽略，可能被重复执行；旧依赖标记也不再阻止任务启动。
已关闭 Issue 中未迁移的旧标记不再被新版 runner 识别。

## 本机状态、仓库配置与工作树目录

`kc config migrate --apply` 还会迁移本机 `~/.iar` 状态、将 `.iar.toml` 改名为 `.kedacode.toml`，并清理
旧版 `kc init` 写入的部分固定默认值。若仓库存在旧 `.iar-worktrees/` 且没有活动的 linked worktree，
同一命令会将其改名为 `.kc-worktrees/`；若有活动 worktree 则拒绝改名。运行 `kc worktree create` 时，
如果旧 `.iar-worktrees/` 仍存在，会继续在该目录创建工作树，不会另建平行目录。

仓库状态与工作树的默认目录现在分别是 `.kedacode/` 和 `.kc-worktrees/`。旧 `.iar/` 状态根存在时，
会话与 findings 继续沿用它，尚未创建的子目录也在旧根内创建；明确配置为旧路径的仓库也不自动改写。若本机同时存在新旧状态目录，或仓库
同时存在 `.iar.toml` 和 `.kedacode.toml`，迁移命令会拒绝自动合并。请先备份并整理，再重试。

## 仍接受的旧名称

Idea Inbox Webhook 新签名头是 `X-KC-Signature`，同时继续接受 `X-IAR-Signature`，便于发送方分批升级。
已推送的 `iar-evidence/*` 分支保持原样；之后创建的证据分支使用 `kc-evidence/*`。历史文档、归档 PRD
和已保存的证据记录保留原文。
