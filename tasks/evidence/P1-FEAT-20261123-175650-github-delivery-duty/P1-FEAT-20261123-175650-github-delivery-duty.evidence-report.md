## 人审导航 / Human Review Navigation

| 要确认的行为 | 呈递物 | 复查 |
|---|---|---|
| 从 Issue 和 PR 两个入口点名查询，答复说清谁该做什么、依据在哪，且没改任何东西 | [两条真实会话及并排快照](https://github.com/ZataZhang/keda/blob/3cb18e10ddd5a5affef4033c8083021cfb5311a7/human-review-checklist.md)；本地可运行 `just prd review tasks/archive/P1-FEAT-20261123-175650-github-delivery-duty.md` | 两份答复中的测试 PR head 同为 `f7d49ee`。GitHub 时间线 Issue、PR 各 2→2，写调用各为 0。 |

执行器已交叉核对实际业务双入口、观察时点、GitHub PR head 和写调用计数。呈递材料可直接在 PR 中打开。 [PR 呈递评论](https://github.com/ZataZhang/keda/pull/436#issuecomment-7138597691)

## 按确认后的判据验证

维护者于 2026-11-25 确认依据新版实测验收并披露旧版对照。独立 verifier 对 RV-1 至 RV-4 给出 **PASS**。实现观察提交为 `98dc2dd0`，独立复核提交为 `a298e95f`，排除验收记录路径后的 tree 相同，为 `683915a16b3027d89a31f6540cdf9b95638dcedd`。 [证据清单和文件哈希](https://github.com/ZataZhang/keda/blob/3cb18e10ddd5a5affef4033c8083021cfb5311a7/evidence.json)

| 验证 | 观察结果 |
|---|---|
| RV-1 双入口只读 | 00:55–00:56 的实际业务查询读取同一提交，Issue 30→30 条，PR 18→18 条，零写入。隔离仓库实测两入口均等待当前检查，并识别另一个场景的真实推送故障。补入异常事件的本地探针按预期失败。 [双入口证据](rv-1-duty-read.md) |
| RV-2 隔离巡检 | 恢复原提交并从 GitHub 读回草稿 PR。已接管目标在补测中保留相同的 30 条直接事件和同一草稿 head；新版复用上轮摘要并关闭自己的巡检。旧版同样停止目标动作。追加本地直接评论的探针按预期失败。[隔离巡检报告](https://github.com/ZataZhang/keda/blob/3cb18e10ddd5a5affef4033c8083021cfb5311a7/rv-2-duty-patrol.txt) |
| RV-3 四题盲评 | 普通组误报 1/4，岗位组 0/3，两组漏报 0/3。按同一模型与材料执行，数据仅代表这四个场景。 [逐题结果](rv-3-replay.md) |
| RV-4 分发和配方 | 56 项定向用例及严格 MkDocs 构建通过。缺失模板引用时，真实安装测试按预期失败。 |
| 默认 `just test` | 同步主线后在实现提交增量跑了 72 项并通过。相同产品树的后续默认增量未选中用例，lint 和命令均通过。 |

两轮旧版对照均等待当前检查并尊重明确接管。接管只写在 PR review 的补测中，旧版多留了一份巡检复核清单，现版记录结论并关闭自己的巡检单。两版都未改目标。成功发布之后的独立执行器监督受组织权限限制，核心发布与读回已完成。

[独立 verifier 报告](P1-FEAT-20261123-175650-github-delivery-duty.verifier-report.md) · [最新 GitHub 检查](https://github.com/ZataZhang/keda/pull/436/checks)
