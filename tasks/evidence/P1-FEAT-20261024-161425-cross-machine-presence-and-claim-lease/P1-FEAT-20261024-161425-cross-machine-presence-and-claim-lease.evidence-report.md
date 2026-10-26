## 人审导航 / Human Review Navigation

| 看什么 | 呈递物 | 10 秒自检 |
|---|---|---|
| 在一台机器上看到两台机器及各自在做的 Issue（rv-1） | `tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/rv-1-kc-peers.txt`；打开：`open "$(git rev-parse --show-toplevel)/tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/rv-1-kc-peers.txt"`。这是终端原始输出；本项没有截图或其他静态图片。 | 表格里 B 那一行的"正在处理"列是 `#42`；JSON 中 `machines` 长度为 2 |

执行者已用两个独立状态目录、两个真实 daemon 与真实 `kc peers` 交叉核对这份输出。对话中可打开工作树里的文本文件；也可运行 `just prd review tasks/archive/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease.md` 定位交互清单。PR/CI：执行者未创建 PR 或触发 CI；这项交付由 runner 后续处理。

## 验证身份

- Candidate implementation tree：`8b8c0d22461ab066fef954d7b88b376a3f3884af`
- Base HEAD tree：`901998256c8daaca6f2fa4ef627150ff76f666ac`
- 四项 RV 各自在最终实现树上重跑；证据命令、输出摘要、expected fail、解释和风险见同目录 `evidence.json`。
- 原始终端文件按 RV item 分开保存；RV harness、假 GitHub/agent 与 tree helper 都位于 `scripts/`，不进入代码 diff，脚本和输出不含凭据。

## 风险优先证据

### 决策一 · rv-2（R3）

负控令假 GitHub 对已过期认领返回新鲜更新时间，真实 daemon 因而不接手；green 场景分别观察到 TTL 前 30 秒仍运行和 TTL 后 30 秒回到队列。旧版无租约认领在模拟 24 小时后仍保持 running，基线 `kc:claim` 解析器读到 host、pid、started_at 与 agent。

- 原始证据：`rv-2-remote-lease.txt`
- Green 要点：`remote_lease_expired_comments=1`；`issue_42_labels=agent/ready`；`lease_marker=absent`（#43）
- Negative control：返回新鲜 `updated_at` 后，#42 仍为 running、过期评论为 0，oracle 按预期失败。

### 决策一 · rv-3（R3）

负控让没有竞争认领的真实 `kc run` 推送了本地裸仓库分支并尝试创建 PR，保护 oracle 按预期失败。Green 场景覆盖 push 前与 PR 前的两次归属确认；续租失败到 120 秒 TTL 的半程后，进程登记簿确认并终止了真实 agent 子进程。

- 原始证据：`rv-3-self-fence.txt`
- Green 要点：子进程 `alive_after_exit=false`；失租后远端 ref `absent`；PR 创建 0 次；失租后的标签写入 0 次（初始领取的两次写入除外）
- Negative control：关闭竞争认领更新后，远端 ref 从 absent 变为 commit，且实际尝试 `gh pr create`。

### 决策二 · rv-4（R2）

负控拒绝删除重复状态 Issue 的 presence 标签，两个真实 daemon 留下两条 open 状态板，唯一板 oracle 按预期失败。Green retry 和并发首次启动都收敛为一个 open 状态板。关闭状态上报后 `kc run` 不更新状态评论但仍续租；同主机名的不同 machine ID 保持为不同机器，远端有效租约不被回收。

- 原始证据：`rv-4-presence.txt`
- Green 要点：`open_presence_issues=1`；`GREEN PRESENCE SWITCH`；`GREEN MACHINE-ID LOCALITY`
- Negative control：`open_presence_issues=2`，要求唯一板的断言按预期失败。

### 双机可见 · rv-1（R1）

负控隐藏 B 的状态评论，真实 `kc peers --json` 仅返回一台机器，长度为 2 的断言按预期失败。去除故障后真实表格与 JSON 均显示两台在线机器，B 正在执行 #42、agent 为 codex，状态 Issue 有两条评论。

- 原始证据：`rv-1-kc-peers.txt`
- Green 要点：表格有 #42/codex；JSON 的 `machines` 长度为 2；`status_issue=#100 presence_comments=2`
- Negative control：隐藏 B 后 `machines=1`，oracle 按预期失败。

## 其他交付验证

- 相关测试：`uv run pytest -o addopts="" tests/test_agent_runner_claim_lease.py tests/test_coordination_heartbeat.py tests/test_machine_presence.py tests/test_agent_runner_reconcile.py tests/test_run_target_admission.py tests/test_agent_runner_claim_arbitration.py tests/test_agent_runner_reclaim.py tests/test_github_client.py` — **138 passed**。
- 配置映射回归：设置 TTL 为 900 秒的认领评论包含 `ttl="900"`。
- `SKIP=check-test-flag just lint --full` — **passed**；`just lint --reuse` — **passed**；`uv run mkdocs build --strict` — **passed**。
- `just test` 根据 testmon 增量结果未选择任何用例；相关八模块已用上方不带 testmon 过滤的命令全部执行。
- PRD acceptance archive-readiness checker — **PASS**。此前 runner-owned verifier 对更早候选树给出 Yellow，其中报告的并发指针评论缺陷已由当前提交修复；最终树仍需独立 verifier 重跑并给出 PASS。PRD 已由 runner 归档；归档记录执行侧交付完成，不代表人工验收。

## 人审清单浏览器检查

清单文件：`tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/human-review-checklist.md`；交互伴侣：`tasks/evidence/P1-FEAT-20261024-161425-cross-machine-presence-and-claim-lease/human-review-checklist.html`。Markdown 与 HTML 的三项标题和选择项已静态核对；模板固定脚本未改。静态 HTMLParser 检查得到 4 张卡、3 组单选、3 个文本框和结果卡。

实际浏览器检查未完成：CUA browser inventory 为空；Edge 和 Chrome-for-Testing 的 app 访问均返回 `Computer Use was not approved`。官方 warning checker 另报 Markdown 3 项、HTML 2 项；这是 checker 对模板注释中 `<h2>` 示例的误读，它从注释开启的伪标题一直匹配到第一张真实卡的闭合标题。执行者没有把静态解析当成 pageerror 或交互验证的替代品。CUA inventory 无浏览器，已安装 Chrome 的 headless/dump-dom 启动均以 134 退出。此项记录为 `REVIEW_INCIDENT / INCONCLUSIVE`，等待 runner 在可用浏览器环境中验证首卡显示、翻页和末页 Markdown 生成。


## 最终树跟进

代码审查修复后的四项 RV 已在同一最终候选树 `8b8c0d22461ab066fef954d7b88b376a3f3884af` 上重跑通过；base HEAD tree 为 `901998256c8daaca6f2fa4ef627150ff76f666ac`。最终相关 pytest 覆盖八个模块、138 passed；最终树的 lint、reuse、MkDocs strict 与 diff check 均通过。RV-4 会断言并发首启后重复 Issue 只留一条指针评论。

此前的 runner-owned verifier 原始报告针对更早的候选树，不能作为本最终树的独立 `PASS`；runner 仍需在最终树上重跑独立 verifier。真实浏览器 smoke 仍是 `REVIEW_INCIDENT / INCONCLUSIVE`。
