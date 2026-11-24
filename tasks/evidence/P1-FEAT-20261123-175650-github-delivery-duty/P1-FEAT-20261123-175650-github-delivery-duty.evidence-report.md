## 人审导航 / Human Review Navigation

**本轮复核：** 交付分支已同步 `main`，`git diff main...HEAD` 只包含岗位说明、巡检配方、测试、文档和交付记录。早期评审提到的 Console 和 supervisor 文件已经不在差异中。通过实测修正了两个问题：先核实旧评审判断，再决定是否要求隔离改动；目标已关闭或合并且无可核验的未结事项时，不再分配当前责任人。

| 验证 | 本轮结果 |
|---|---|
| RV-1 · 已安装技能的真实入口 | 新 Codex 会话分别读取 [Issue #430](https://github.com/ZataZhang/keda/issues/430) 和 [PR #436](https://github.com/ZataZhang/keda/pull/436)，两者关联及 head 匹配，命令轨迹无写入，前后时间线为 30 和 8 条。旧技能对比把历史范围判断列入待办。原 oracle 的待检查现场、发布失败 PR 入口与主演示仍需单独呈递。 |
| RV-2 · 隔离巡检 | 已在私有测试仓库由专用 daemon 调度，人工接管目标的时间线保持一致。首轮恢复样例因缺少真实认领记录被拒绝，随后已用真实 runner 制造并修复一次推送故障，继续核对恢复回读及旧配方负控。 |
| RV-3 · 冻结材料与独立盲评 | 四个现场、两组各四次新会话，评分者仅见随机化 X/Y。普通组误报 1/4，值班员组 0/3，均漏报 0/3。岗位组四题耗时 23.50、22.46、24.26、22.34 秒；普通组 16.08、15.17、14.51、13.39 秒。材料、分组映射和逐题评分保留于本地证据包。 |
| RV-4 · 安装、配方与文档 | 在实现提交 `9da79d9b` 上，56 项定向测试和严格文档构建通过。将模板引用改成不存在的参考文件时，目标测试因缺少 `references/duty.md` 失败。 |
| 默认测试命令 | `just test` 首轮受继承的 `KEDACODE_NO_UPDATE_CHECK` 干扰，升级提示的 6 条测试失败。移除该变量后在 `9da79d9b` 使用仓库默认增量档复跑并通过。 |

下方为早期执行及评审记录，现状以上表和后续独立复核为准。

| 呈递项 | 真实状态与应观察值 | 本地记录 | 打开方式 | 交叉核对 |
|---|---|---|---|---|
| RV-1 · 首轮 Issue 与 PR 真实会话尝试 | **首轮 `REVIEW_INCIDENT / INCONCLUSIVE`。** 期待看到观察时点、状态、来源、下一责任人与动作、复查条件；查询前后 timeline 一致且写调用为 0。 | [rv-1-duty-read.md](rv-1-duty-read.md) | 打开同目录的记录 | 首轮 Codex 会话在 workspace routing discovery 阶段超时。Issue 线索：[GitHub #430](https://github.com/ZataZhang/keda/issues/430)。 |

主演示脚本要求录制真实 Codex 会话与 GitHub 读数。本轮没有真实会话输出、截图或录屏，因此没有内嵌 mock 图片；本地 `rv-1-duty-read.md` 是阻塞记录，不是替代演示。需要人工看到的预期字段和零写入对照仍待 RV-1 完成。

### 执行器证据导航

| RV / 检查 | 结果 | 证据 | 已交叉核对 |
|---|---|---|---|
| RV-1 · 手动只读查询 | `REVIEW_INCIDENT / INCONCLUSIVE`；真实查询、timeline 对照、负控和主演示未执行。 | [rv-1-duty-read.md](rv-1-duty-read.md) | 否 |
| RV-2 · 隔离巡检 | `BLOCKED / INCONCLUSIVE`；无隔离仓库与专用 runner，未对业务 Issue 写入。 | [rv-2-duty-patrol.txt](rv-2-duty-patrol.txt) | 否 |
| RV-3 · 四题盲评 | `INCONCLUSIVE`；未冻结四题，未运行两组盲评。 | [rv-3-replay.md](rv-3-replay.md) | 否 |
| RV-4 · 安装路由、配方和文档 | 负控让目标测试按预期失败；修复后 54 个相关测试通过，MkDocs strict 与 full lint 通过。 | [rv-4-negative-control.txt](rv-4-negative-control.txt)、[rv-4-install-docs.txt](rv-4-install-docs.txt)、[rv-4-architecture-audit.txt](rv-4-architecture-audit.txt)、[rv-4-tree-identity.txt](rv-4-tree-identity.txt) | 是；见下方检索与测试结果 |

### RV-4 自查记录

- 两份 recipe 的 `duty` 检索各命中一次：模板在 `src/backend/core/use_cases/loop_builtin.py`，仓库配方在 `tasks/loops/keda-blocker-monitor-30m.md`。两处只引用岗位说明并保留人工接管停止条件；SHA、Attempt History、缺证与交接判断细节只保存在 `references/duty.md`。模板和仓库配方测试还断言不复写 SHA / Attempt History 检查。
- `references/` 现有 13 个 Markdown 文件；`docs/guides/agent-runner.md` 写明 13 份。测试核验路由表、受管文件集合、全部说明可安装，和触发词可由 frontmatter 发现。
- 变更路径已核对：implementation paths 都在 §7.2 Change Impact Tree，包括根据首次全套测试失败补入的 `tests/test_kedacode_operator_skill.py`。未新增 CLI 子命令、后端服务、数据库或存储。PRD 与本证据包属于交付记录。
- `rv-4-tree-identity.txt` 记录了验证所基于的 worktree HEAD 和实现 / 测试文件的 SHA-256；最终提交 SHA 由 runner 提交后生成，目前尚不可得。
- 完整 `just test` 在首次执行时为 3 failed / 5633 passed / 1 skipped，失败原因均为新增 reference 后仍硬编码 12；修复后相关套件为 54 passed。因为完整执行耗时 481.44 秒（超出默认 300 秒复跑上限），最终树的完整 `just test` 保持未验收。
- `just lint --reuse` 通过。最终全 lint 使用 `SKIP=check-test-flag` 绕开陈旧的测试标记检查，所有实际 lint hook 通过；首次格式化 hook 自动修正后复跑绿。严格文档构建完成，日志中的既有未列入导航页面和 mkdocstrings 提示不影响退出码 0。

## 结论

功能实现与 RV-4 本地门禁可复核。执行器验证尚未完成：RV-1 因会话路由故障，RV-2 因无隔离 runner，RV-3 因无冻结盲评材料；完整 `just test` 未在 300 秒预算内完成最终树复跑。此包不是 verifier `PASS`，也不表示 Human-Confirmed 已完成。
