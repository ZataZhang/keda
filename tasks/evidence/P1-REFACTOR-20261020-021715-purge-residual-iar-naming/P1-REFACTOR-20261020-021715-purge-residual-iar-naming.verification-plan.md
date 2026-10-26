# #318 最终验证计划

按风险顺序执行 RV-2、RV-1、RV-3/4、RV-5、RV-6，逐项保留反例与正例。原始文件只进入本地与孤立 evidence 分支；源码分支只保留 Markdown 报告。

- RV-1：`scripts/rv-1-live.py`。显式指定 `KC_RV_SANDBOX_REPO` 与 `KC_RV_BASELINE_SOURCE`，复用本机 gh 登录；生产 CLI 创建、GitHub 保存、fresh gh 读回与生产依赖门禁必须贯通。所有创建的 Issue 在 finally 中关闭，不运行 Agent。
- RV-2：`scripts/rv-2-current.py`。复用 `rv_harness.py` 的持久化 gh 替身；真实 CLI、Git、进程扫描与消费者不替换。检查迁移前负控、只读预演、只替前缀、新进程认领、重跑零编辑、新状态目录活动登记拒绝及实际 kc daemon 拒绝。
- RV-3/4：`rv_harness.py` 的真实 worktree 场景。`KC_CLI_PATH` 指向当前环境的 kc，`SCENE_ROOT` 放到独立临时目录；每项先负控再正例，用真实 Git 元数据与 status 读回。
- RV-5：`scripts/rv-5-signature-control.py`，故障注入与生产路由正例顺序执行，finally 恢复源码；禁止与其他测试并发运行。
- RV-6：`scripts/rv-6-guard-control.py`，临时插入白名单外旧名后 guard 失败，移除后通过；不得修改 guard 放行。
- 补充：执行真实 Validation Gate 工作流脚本，生产 writer 生成的未完成 kc 清单须拒绝；执行新目录 PID/锁回归测试与最终受影响 CLI/API 测试。
- 最终门禁：full lint、reuse、相关测试、strict MkDocs、最终 head 远端完整 CI、干净已提交树的独立 verifier。全部通过才签核机器项并 squash 合并。

无前端交付；不使用组件预览或截图代替以上真实入口证据。最终源代码身份见 `rv-0-source-identity.txt`；报告与验收记录变更不得改变实现或 oracle 范围。
