# rv-1 真实入口验证状态（已跑通）

捕获代码版本：分支 `issue-274` 交付树（功能提交 `25b8d58b` + 两轮评审修复提交）；四张截图与绿/红证为最后一轮
（Pre-PR Review 修复轮：证据目录枚举失败降级 + 证据降级文案）后由仓库自身门禁 `scripts/rehearse_gates.py`
复跑 `rv1_real_entry.sh --negative` 重新生成的产物（4 张 PNG 与 green/red 原文的 mtime 即该次复跑）。
运行时间：2026-10-11（UTC）。

## 结论

rv-1 要求的「真实 `kc console` + 真实浏览器 + 真实 git worktree 与磁盘证据」已在本工作区
跑通，**不再存在环境阻塞**。此前一轮记录的缺口（端口 3000 被占用、缺 `.env.run-state`）经
复核是误判：`kc console` 硬编码回环监听并在 20 端口窗口内自动挑空闲端口、认证为空实现、
Playwright 可用 `PLAYWRIGHT_BASE_URL` + `PLAYWRIGHT_SKIP_STACK_BOOT=1` 直接指向它。因此本轮
按 PRD §7.6 的原始保真度补齐了真实证据，而不是继续以 TestClient 结果代替。

## 复现命令

```bash
bash tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/scripts/rv1_real_entry.sh --negative
```

脚本做五件事：`just console-sync` 构建前端产物 → `rv1_fixture.py` 重建隔离 fixture → 以
`KEDACODE_CONFIG`/`IAR_CONFIG` 指向 fixture 登记启动真 `uv run kc console --no-browser` →
跑 `tests/playwright-e2e/tests/workflows/backlog-prd-progress-real-entry.no-auth.spec.ts`
（零 `page.route` 拦截）→ 换成不含本改动的 main 构建重跑同一 spec 作为负控。

## 四态实际观察结果

| 状态 | 清单 | 影响树 | 证据 | 关键判别 |
|---|---|---|---|---|
| 运行中（工作区命中） | `3/12` | `~7/12 ?2` | `2/3` | 主仓库同名副本是 `1/12`，读到 `3/12` 即分支优先 |
| 已归档（残留工作区） | `12/12` | `-` | `3/3` | 残留工作区副本是 `0/12`，读到 `12/12` 即终态取主仓库 |
| 无计划旧 PRD | `5/12` | `-` | `-` | 确认无计划显示 `-` 而非伪造 `0/0`，共 2 个文件 |
| 无基准（trunk 仓库） | `4/12` | `-` | `1/3` | 不可比较显示 `-`，不是 `~0/12` |

证据标签：运行中显示「计划 3 项 · 已产出 2 项（共 4 个文件）」，rv-1 与 rv-3 分组内均为
工作区文件并带「未合并」徽标，rv-2 分组为待产出，无归属文件落到「其他」。spec 另断言 strip
内三个 chip 的 `title` 提示都包含「弱信号」，确保读数不会以「已完成」的口气被读走。

影响树不可比较 / 不可用时随读数给出原因说明，不再只留一个孤零零的 `-`：已归档
「归档终态，不参与分支比较」、无工作区「未发现执行工作区」、无基准「无有效比较基准，
无法比较」、查询或计算失败「查询或计算失败」；spec 对相应四态逐一断言该说明在场，
可计算的运行中态则断言不出现说明。

## fresh-state 探针

把运行中工作区 PRD 副本的第 3 个勾选项取消 → 重新走一遍真实入口 → 清单 chip 变为 `2/12`；
恢复后再次进入回到 `3/12`。证明 `/progress` 每次现读磁盘，不是列表快照的缓存值。

## 负控（红证）

`rv-1-negative-control-red.txt` 是把 `src/backend/api/static/console` 换成 main 工作区构建
（脚本先断言该构建确实不含 `prd-progress-strip`）后，同一 spec 的失败原文：

```
Error: expect(locator).toHaveText(expected) failed
Locator: getByTestId('prd-progress-checklist')
Error: element(s) not found
```

与 PRD §7.6 rv-1 的 `expected_fail`（「负控下头部没有三个读数 chip」）逐字对齐，说明该验证
能判别本改动的缺失，绿证不是恒真断言。

## 复跑注意（bash 3.2 半截绿灯）

runner 以 `bash -lc` 复跑本命令时，登录 shell 会解析到系统 `/bin/bash` 3.2。它在 UTF-8 环境下
把 `$VAR（` 的全角首字节并入变量名，触发 `set -u` 展开错误：脚本当场中断，但退出码仍是 0。
本轮取证曾因此只跑到第 1 节就「通过」。现在两份脚本的变量一律 `${}` 包裹，且 `lib_console.sh`
的 EXIT 陷阱要求脚本显式 `mark_completed`，未走到收尾一律判失败——复跑时若看到
「❌ 脚本未走到收尾即退出」，说明有步骤没执行完，不能当作绿证。

## 评审修复轮补充验证

评审发现两处展示层问题并修复（影响树 `-` / 「暂不可用」随读数给出原因说明；证据分组徽标
以后端 `produced_ids` 为准，与汇总行不再矛盾）。修复只动前端展示与 e2e 用例，rv-2 / rv-3
的 pytest oracle 路径未变；受影响的是本文件的真实浏览器证据，已按修复后行为重跑：

- `rv1_real_entry.sh --negative`：绿跑 5 passed（四态各自断言原因说明在场、运行态断言不出现
  说明），负控仍按预期 rc=1；四张截图与 endpoint 转存件为本轮重生成产物。
- 补充：`tests/smoke/backlog-prd-progress.spec.ts`（读端点 fixture 顶替）在本 worktree 也可
  真实跑通——把 `PLAYWRIGHT_BASE_URL` / `PLAYWRIGHT_API_BASE_URL` 指向 `kc console` 的本机
  回环地址、并以任意本地 operator 凭据登录即可（console 的 `/api/auth/login` 接受任意凭据，
  `/app/dashboard` 即为登录态首页），无需另行起带鉴权的应用栈。修复轮以该方式复跑
  `6 passed`，覆盖新增的「清单归属与文件名前缀不一致时分组徽标不与汇总矛盾」用例。

## 产物清单

- 四张一级截图：`rv-1-running-detail.png`、`rv-1-archived-detail.png`、
  `rv-1-degraded-no-plan.png`、`rv-1-no-base-detail.png`
- 端点响应原文（浏览器实际拿到的 `/progress`）：`rv-1-{running,archived,noplan,nobase}-progress-endpoint.json`
- 隔离登记快照：`rv-1-registry-repositories.json`
- 绿证/红证原文：`rv-1-real-entry-green.txt`、`rv-1-negative-control-red.txt`

## 验证层级

`real console`（真实进程 + 真实端点 + 真实 git worktree + 真实磁盘证据），非 component preview、
非 mock。截图为 1440x1200 全页渲染；本流程不涉及 Dialog/Portal，读数与证据标签都是详情页内联
渲染，生产边界完整。
