# Issue #235 — 验证与交付报告（独立 verifier PASS，执行侧交付完成）

## 人审导航

- rv-1：最终提交上的独立 CLI 与混合 daemon 真 GitHub 探针，见下方实际 Issue/PR 链接及 fresh API 状态。
- rv-2：同轮关联、非原子故障与恢复矩阵，见下方测试边界与独立 verifier 报告。
- rv-3：最终发行资源 `kedacode-operator/SKILL.md`、`references/run-once.md`、`references/daemon.md` 及 `docs/guides/agent-runner.md`。

## 最终产品树

产品提交：`d59ff9cc02924eaf075b52fa9c0a264d4c00ed6b`。
Git tree：`6dbe4cbeea50681e24ed688d25fb17f431c9342f`。
产品指纹：`405228cd8106a962071adfd3438b3c4182f9f2c240210ac3ac2a808dfafca704`。
该指纹包含 src/tests/docs 和配置、依赖、just 文件；后续归档/证据提交须重新核对产品指纹相同，不把报告提交冒充产品验证提交。

## 通用门禁

- `CI=1 just test all`：3578 passed，1 skipped，173.80 秒；CI 强制全量，不采用增量跳过。
- `SKIP=check-test-flag just lint --full`：PASS，仅前置检查的循环测试标记暂跳；真实提交 pre-commit 中 `check-test-flag` PASS。
- `just lint --reuse`：PASS。
- `uv run mkdocs build --strict`：PASS；既有两个 anchor 提示是 INFO，没有新增 warning。
- `git diff --check`、产品提交 pre-commit：PASS。
- `just lint --repo`：PASS，最终 clean 产品树选择 517 passed、360 deselected、39.67 秒；该选择不替代上方全量 CI suite。

原始日志按同名保存到本目录的 `raw/`（gitignore 排除）；三份 Markdown 报告随交付提交。

## rv-1 真 GitHub 入口

最终 fixture：`live-probe-20261008-054041-410`，锁定上方 d59ff9cc/6dbe4cbe 产品身份。fresh API 与独立 verifier 新 GET 的结果一致：

| 真实入口 | Issue / Draft PR | PR HEAD（等于 fresh branch SHA） | 判别结果 |
|---|---|---|---|
| 独立 CLI，标签选 DIRECT | [Issue1](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/issues/1) / [PR5](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/pull/5) | `c7dbe4605d2780d06bce371cd41316df79c4994b` | 一次 builder；DIRECT marker；标签消费；终态 review |
| 实际 daemon，标签选 DIRECT | [Issue2](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/issues/2) / [PR6](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/pull/6) | `61720ed1fe24e259f7e24f3f8a96606ad4519008` | 一次 builder；DIRECT marker；标签消费；终态 review |
| 同 daemon，无标签 NORMAL | [Issue3](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/issues/3) / [PR7](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/pull/7) | `f3799b63efbbe3f7bcc64f2597ac3a36950f717d` | 一次 builder；无 DIRECT marker；两次真实仓库验证；终态 review |

三个成功 Issue 的 `test/preserve` 均保留，两个 DIRECT 均未运行仓库验证。CLI 在启动 daemon 前已独立发布及消费标签，不把后续恢复冒作 CLI 成功。

[PRD 负例 Issue4](https://github.com/ZataZhang/keda-direct-probe-20261008-054041-410/issues/4)：首次 daemon 观察在认领后被 SIGTERM，不能证明已拒绝。随后同一已认领任务经实际独立 CLI 继续，于 05:50:44 收到 core 的 PRD-backed DIRECT 拒绝错误，退出码 1；fresh 状态 failed，direct-pr 与 test/preserve 保留，builder/verification 计数不变，PR 总数仍为 3且无 issue-4 PR。见 `negative-terminal-evidence.json` 与 continuation 日志。该补证属于实际 CLI 完成拒绝，不称为 daemon 已完成负例。

正例 fresh JSON、remote branch refs、labels sync 定义与负例终态日志均按原始观测保留；独立 verifier 最终 PASS；另做 fresh 负例 GET，见 `independent-negative-final-fresh.json`，确认 failed、保留标签、无 issue-4 PR、计数不变。

边界：实际 CLI parser、严格 preflight、认领仲裁、daemon、worktree、commit proxy、git push、Draft PR 与标签 API。独立 CLI 不带 `--direct-pr`；daemon 同一队列包含带标签 DIRECT、无标签 NORMAL、带 PRD 的拒绝任务。fixture 用确定性外部 builder 替代 LLM，关闭 memory、AI review/generated body、runner independent validation 与内联 supervisor；NORMAL 仓库验证命令真实执行并计数。证据证明发布 plumbing，不证明 LLM 实现质量。

## 四项已修复的独立审查问题

1. label 删除接口成功后仍可能未实际删除：补 fresh label 回读，存在则保持 cleanup pending，不宣称完整消费。
2. workflow 终态与完成顺序：正常和恢复均至 review，最终切换成功后才完成检查点；失败保持可恢复 running。
3. checkpoint 权限与权威读取：拒绝无权限评论伪造 DIRECT；评论查询失败、权限未知/失败均 fail-closed；合法操作作者继续恢复。
4. 历史 PR 不存在基线：只允许严格成功空列表证明不存在；非零、空输出、畸形/歧义数据不落 candidate，不关联历史 PR。

最终独立外部 adapter/core 回归：37 passed，0.19 秒；保留历史失败 proof 为修复依据，不把它们计为当前失败。

## rv-2 故障与恢复矩阵

最终修复还必须证明：真实 adapter 筛选无权限伪造检查点，授权作者同 payload 可恢复 DIRECT；评论查询非零、权限查询失败、未知/缺失元数据均在实际 ready handler 的 builder 前停止。使用现有评论端口的 opt-in trusted_only/body_contains，不改变默认尽力读取用途。本人作者使用服务端 viewerDidAuthor；其他作者经 fresh 仓库权限查询确认 triage/write/maintain/admin 或对应布尔权限，不能仅依据 authorAssociation。权限缓存仅限单次读取，无关评论不触发查询。公开 read 用户与 owner 的实际 GitHub permission 响应已对照；没有第二账号凭据，不把替换元数据的故障测试称为真实多账号运行。


实际发布/关联/消费代码和 ready/running/blocked/standalone recover 用例运行；Git/CAS 部分为测试边界。GitHub adapter 把 comments、labels、PR 和计数写磁盘，后续独立客户端重新读取。

| 场景 | 判别性观察 |
|---|---|
| PR 已持久创建但响应丢失 | fresh client 关联同轮 PR，创建总数保持 1，不重建 |
| 同 branch/head/marker 的历史 PR | 没有本轮创建前基线/关联则拒绝消费新选择 |
| repository/Issue/branch/head/URL 不匹配、foreign checkpoint | fail-closed；不删除标签、不采用历史关联 |
| checkpoint 写入未持久化 | 创建前 fresh 回读发现不符，PR 创建数 0 |
| 删除 API 报错、或返回成功但没有删除 | fresh labels 仍存在，报告 cleanup pending；下一客户端仅交接原 PR |
| 标签已删除、workflow 写失败 | checkpoint 保持未完成，fresh 客户端沿用 DIRECT，只完成同 PR |
| 初次最终 review 切换失败 | 两条 publication finish × supervisor 开/关，保持 running 和未完成 checkpoint；后续 cleanup 不执行 builder/push/just/create |
| ready/running/blocked 与独立 recover | 标签存在/已删、当前正文已新增 PRD、supervisor 开/关，恢复至 review；不启动新 DIRECT |
| discovery 与认领间 checkpoint 移除/完成/身份变化 | 只补清理的请求拒绝转成新的构建或发布 |
| claim 输家、fresh unmet dependencies、不可读正文、PRD/FAST 冲突 | 执行/创建/消费无副作用；混合队列隔离拒绝 |
| sibling 与 fallback | Issue 间无档位共享；当轮固定档位但正文/依赖继续 fresh |

合法负控：同一输入仅移除 fresh direct-pr label，DIRECT 判定翻为 NORMAL；删除失败/未实际删除返回成功均使“完整消费”断言不成立；不通过修改 guards、放宽生产断言或制造代码假故障取绿灯。

独立审查曾确认的终态漏洞已修复：旧实现正常 DIRECT 最终 review，但恢复 supervising，而且最终 review 前就完成检查点。修复统一 review，并在最终切换成功后完成检查点。旧树 `1f301299` 的 live PASS 仅为诊断，不能替代最终产品树取证。

另一次独立实际 adapter/core 探针证实，普通 PR 读取失败返回 None 会误写旧 PR 不存在的基线；查询恢复后可能绑定历史同 head PR。最终源用 opt-in require_success 严格查询修复，只有成功 [] 证明不存在，错误、空输出、未知/歧义响应都拒绝。15 项新测试覆盖 candidate 不持久化、不错误关联及合法空列表，默认普通读取接口保持兼容。

## rv-3 配置与协议全文审查

默认/自定义/关闭标签配置经过实际模型和 factory 传播；labels sync 创建非 workflow 标签，状态切换保留其他标签。实际私有仓库的 `kc labels sync` 和 fresh API definitions 作为 live 证据。

发布协议保留原 marker `<!-- iar:direct-pr issued=N -->`、CLI 单目标限制及声明。档位判据包括公共调用契约、跨层、schema/迁移、安全边界、范围不明或证据失败时使用 NORMAL；FAST/DIRECT 不能伪造、削弱或省略必要最终树验证。沿用授权，不新增理由参数、全局 daemon 旁路或自动合并。

## 限制与编码对账

- GitHub 创建、标签删除、workflow 更新不原子；同标签 cleanup 窗口内并发重加不受支持。窗口内外部手动创建完全同 branch/head/marker PR 也无原子归属保证，已记录操作者协调义务。
- 所有潜在认领者需升级并重启；`kc recover` 保留本地干净工作树要求，不能把跨机器选择宣称为无需工作树的远程恢复。
- API fresh 文本不是浏览器 user-flow 截图；无新 UI，按用户授权不做截图呈递仪式。
- 故障为 durable adapter 注入，不宣称真实 GitHub 服务发生故障或真实强杀进程。真 GitHub 探针只证明正常独立 CLI/daemon 路径。
- 新增规则/请求集中复用，成组参数收敛到对象；新增模块低于 800 非空行。历史 handlers 保持 999、publication 保持 953 非空行，旧入口多参数签名为兼容保留；未重写整个历史模块。full/reuse/架构检查通过。
- 部分早期针对性故障复现先于 full/reuse lint，不能声称“任何测试之前都跑绿 lint”；最终 full/reuse lint 在最终全量测试前通过。未修改 guards，导航沿用已有 guide，不新增 mkdocs 页面。

## 执行侧交付

独立 verifier 最终 PASS，最终树 live 与负例终态证据已齐备；executor 可据证据勾选非人工验收、完成 Final Reconciliation 并归档。Human-Confirmed 保留空框，归档横幅为待人工验收。

[验证计划](verification-plan.md) · [独立 verifier 报告](verifier-report.md)。最终报告及证据归档后复核产品指纹相同；若产品改动则重新验证受影响项。
