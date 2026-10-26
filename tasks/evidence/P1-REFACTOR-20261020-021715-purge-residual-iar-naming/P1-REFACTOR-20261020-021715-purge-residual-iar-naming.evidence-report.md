# #318 最终复核证据（2026-10-26）

对应 [PR #318](https://github.com/ZataZhang/keda/pull/318) 与 [Issue #313](https://github.com/ZataZhang/keda/issues/313)。本次没有前端改动；证据层级为 CLI real entry、真实 Git / filesystem、API route 和 GitHub live integration。

## 风险优先的人审导航

1. 协作标记升级：查看 `rv-1-live-github-current.txt`、`rv-2-marker-migration-current.txt`、`rv-2-active-daemon-current.txt`。必须全部停机后迁移 open Issue / PR；旧标记不再读取。
2. 目录策略：查看 `rv-3-clean-repository.txt` 与 `rv-4-legacy-directory.txt`，以及对应 negative-control 文件。新仓库使用新目录；旧目录继续复用，不自动搬动。

真实 GitHub 沙箱为用户明确授权创建的私有仓库 [kedacode-validation-sandbox](https://github.com/ZataZhang/kedacode-validation-sandbox)。使用隔离配置、禁用 AI 内容生成；测试 Issue 完成后关闭。凭据从本机 gh 登录态读取，只放入子进程环境，未写进文件、Git 或证据日志。

## 本轮修复

- 合并最新 main，保留 daemon 参数限制、批量默认值、新初始化检查名和 #321 的真实 backlog builder 测试修复。
- 修正 Validation Gate 工作流仍读旧前缀的遗漏。故障复现证明生产生成的未勾选 kc 清单曾被当成“不存在”而通过；现有工作流脚本与生产清单 writer 的四个组合测试现在验证未完成拒绝、全部完成允许、零项目允许。
- 复验 RV-2 时发现旧状态目录已移除后，新目录的登记 PID / 锁未参与预检。现同时检查新旧目录；两项回归测试和真实 CLI 验证均证明活动登记会阻止远端改写。真实 kc daemon 也通过真实进程扫描阻止 apply。

## Realistic Validation 结果

| RV | 实际边界与判别力 | 结果 / 原始文件 |
|---|---|---|
| RV-1 | 新版与改名前版各经生产 kc CLI 写真实 GitHub；fresh gh 读回；依赖 OPEN 时生产 dry-run 阻塞，关闭后新 GitHub client 判定满足 | PASS，`rv-1-live-github-current.txt` |
| RV-2 | 仅替外部 gh 传输，CLI / 进程扫描 / GitHub client / 持久 JSON / fresh kc 消费都保真；有迁移前负控、预演、apply、重复 apply、登记 PID 和实际 daemon 拒绝 | PASS，`rv-2-marker-migration-current.txt`、`rv-2-active-daemon-current.txt` |
| RV-3 | 全新 Git 仓库经生产 CLI 创建工作树；读取实际路径、Git 元数据、干净状态；旧常量负控失败 | PASS，`rv-3-clean-repository.txt`、`rv-3-negative-control.txt` |
| RV-4 | 预置旧目录后真实 CLI 继续复用；禁用旧目录识别的负控出现平行目录并失败 | PASS，`rv-4-legacy-directory.txt`、`rv-4-negative-control.txt` |
| RV-5 | FastAPI TestClient 穿过真实签名路由；新旧有效 HMAC 202、缺头 401；alias 故障注入使新头失败，结束后源码恢复 | PASS，`rv-5-signature-headers.txt`、`rv-5-negative-control.txt` |
| RV-6 | 真实 guard 对临时白名单外旧名报错；移除探针后同一 guard 通过；未修改 guard 本身 | PASS，`rv-6-guard-green.txt`、`rv-6-negative-control.txt` |

原始文件和 oracle 脚本保留在同目录；manifest `evidence.json` 也仅作本地 / evidence 分支产物，源码分支只提交 Markdown 报告。独立 verifier 的结论单独记录，执行器不代写 PASS。

## 仓库验证

- 初次合并 main 后的冷缓存 `just lint --repo` 执行 4995 passed、1 skipped（617.54s）。运行期间新增了后续两项修复，因此该数字不声称覆盖最后的新增测试。
- 最终代码上显式禁用 testmon 执行受影响入口：`tests/test_validation_gate_workflow.py tests/test_state_home_migration.py tests/test_cli_config_migrate.py tests/test_agent_runner_cli.py tests/test_backlog_api.py`，223 passed（32.58s），包含新增六项用例。
- 最终代码的 full lint、reuse、架构与规范一致性检查通过；`just lint --repo` 再运行时命中本地 test 标记，未把该次“跳过测试”算作执行证据；strict MkDocs 构建通过。
- 远端最终 head 的完整 CI 仍须成功，合并前不沿用旧 head 的检查结果。

## 身份与限制

候选提交与 Git tree 写入 `rv-0-source-identity.txt`，由独立 verifier 对照干净提交检查。此报告只总结执行证据，不替代独立 verdict。后续若改动迁移、标记构造、消费者或断言，必须重跑受影响 RV。

证据覆盖本机与指定私有沙箱；无法判断其他电脑是否仍有旧 runner 运行，实际升级前仍需维护者按升级指南停止全部机器上的 runner。合并源代码不执行生产仓库标记迁移。
