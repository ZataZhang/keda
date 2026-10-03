# 人工验收清单 · 日志配置的健壮化与长驻进程跨天轮转

- PRD：`tasks/pending/P0-BUG-20260930-145323-logging-config-robustness.md`
- Issue：https://github.com/ZataZhang/keda/issues/175 · PR：由 runner 在本清单之后创建（合并即视为验收，见每题末尾）
- 状态：**代码与证据已交付，等你对下面 3 个决策 + 1 项呈递过目表态**。除你写明差异外，合并 PR 即表示确认这 3 个决策并授权合并后归档 PRD。
- 交互版（点选后自动产出可复制的回执）：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/tasks/evidence/P0-BUG-20260930-145323-logging-config-robustness/human-review-checklist.html"`

**怎么回复**：每题只需回答三种之一 —— `同意` / `改成 X`（写明你要的另一个选项）/ `有差异：<你的说明>`。可以只回一句"三题都同意，呈递已看过"。

**术语先解释一句**
- *长驻进程 / daemon*：`iar loop-daemon`、`iar registry start` 拉起来的 runner 与 review-daemon，会连续跑很多天不重启。
- *日切*：进程跨过一次午夜、开始写新一天的日志文件那一刻。
- *handler*：Python 标准库 `logging` 里"把日志送到某个去处"的组件；keda 用了两个——一个送终端、一个送日文件。
- *root logger*：所有模块 logger 的公共父节点，挂在它上面的 handler 决定全进程的日志去向。

---

## 决策一 · 跨天换文件的方式（要不要保住现有文件名约定）

**你在拍板的事**：为了不让长驻 daemon 把第二天的日志继续写进昨天的文件，我们准备自己写一个"每次写日志前先看日期"的小 handler，代价是日志模块里多一个自定义类。**如果判错**：另一条路（标准库 `TimedRotatingFileHandler`）会把"今天的文件叫什么"从 `app-YYYY-MM-DD.log` 变成 `app.log` + `app.log.<日期>`，`iar logs` 的回退提示与既有按日归档习惯都要跟着改。

**PRD 原话（§2 决策一）**：
> **决策一：日文件切换方式——保留 `app-YYYY-MM-DD.log` 约定，改用"按日自行切文件"的 handler，可以接受吗？** …… **请确认：** 接受"自定义按日 handler + 保留命名约定"，还是要求"用标准库轮转、接受命名变化并同步改 `iar logs`"？**验收：** 跨天（或秒级模拟）后两个文件名均为 `app-YYYY-MM-DD.log`，且 `iar logs` 能找到当天文件。

**展开说**：文件名保持不变的收益是运维侧零学习成本——`cat logs/app-$(date +%F).log`、`iar logs` 的回退提示、外部归档脚本都不用改。代价是日志模块新增一个私有类 `_DailyFileHandler`（不导出为公共 API），并且它必须是 `logging.FileHandler` 的子类，这样既有对"root 上有 FileHandler"的断言继续成立。**另一条需要你知情的设计**：跨天时如果新文件开不出来（只读挂载、权限变更、fd 耗尽），日志会**继续写进原来的文件并提示一次**，等目录恢复后自动补上切换——而不是把异常抛回业务代码。最坏情况因此是"日志晚一天归位"，换来的是"日志故障不会让 daemon 的业务调用失败"；旧文件收尾失败（磁盘写满之类）同样只丢弃那一段缓冲，切换照常完成。最坏情况因此是"日志晚一天归位"，换来的是"日志故障不会让 daemon 的业务调用失败"。

**证据**
- `.iar/evidence/rv-2-daily-rotation.txt`：红跑（改动前）只有一个文件、跨天后的行仍写在里面；绿跑有两个文件，`dir_listing` 显示各自只含本天的那一行，`handler_opened_file` 跨天后变成次日文件名，文件名全部匹配 `^app-\d{4}-\d{2}-\d{2}\.log$`，没有出现 `app.log.<date>`。同一段还含"把日志目录改成只读后跨天"的红/绿对照：改动前的写法会把 `PermissionError` 抛回业务调用（每条都抛），现在只提示一次并继续写旧文件。
- `.iar/evidence/rv-7-daily-file.txt`：真实 CLI 跑完后目录里出现 `app-<今天>.log`（`CONVENTION OK`），默认配置下该文件就是 `iar logs` 回退提示指向的文件。
- 查看：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-2-daily-rotation.txt"`
- 复跑：`bash .iar/evidence/scripts/rv2_daily_rotation.sh`（在 worktree 根目录）

**你的选择**：☐ 同意"自定义按日 handler + 保留命名约定 + 日切失败保留旧文件继续写" ☐ 改用标准库轮转并接受命名变化 ☐ 日切失败应直接把异常抛给调用方 ☐ 有差异：＿＿＿

---

## 决策二 · 非法日志级别从"启动即崩"改成"降级 + 警告"

**你在拍板的事**：`LOG_LEVEL` 写错时（小写、错拼、空值）进程不再拒绝启动，而是用默认级别 `INFO` 继续跑，并留下一条明确警告。**如果判错**：错误配置不再"响亮地失败"，需要有人从日志里的警告发现它。

**PRD 原话（§2 决策二）**：
> **决策二：非法日志级别的语义从"硬失败"改为"降级 + 警告"，可以接受吗？** …… **请确认：** 接受"降级 + 警告"（服务优先），还是要求"保持硬失败，但把报错信息改得可读"？**验收：** `LOG_LEVEL=bogus` 时进程退出码 0 且出现降级警告。

**展开说**：今天的实际后果是"一个手误让 `iar` 整个起不来"（`getattr(logging, config.log_level)` 直接抛 `AttributeError`），把配置写错升级成了服务不可用。降级方向选 `INFO` 而不是 `DEBUG`，是为了不改变默认可见性；警告必须可见——现在它同时打在终端并写进当天日志文件。**大小写手误的口径请一并确认**：`LOG_LEVEL=info` 这类只错在大小写/空格的值，我们**按你要的那一档生效**（`debug` 仍是 DEBUG，不会悄悄变成 INFO），同时留一条可见提示 `日志级别 'info' 不是 logging 预定义名，已按 INFO 解析`；完全无法解析的值（错拼、空值）才走"降级为 INFO + 无效日志级别警告"。

**证据**
- `.iar/evidence/rv-1-invalid-level.txt`：同一条真实命令 `LOG_LEVEL=bogus uv run iar agent doctor claude --json`，在改动前的源码副本上 `exit_code=1` + `AttributeError`；在修复后的 worktree 上 `exit_code=0` + `无效日志级别 'bogus'，已降级为 INFO`；`LOG_LEVEL=info`（小写）`exit_code=0` 且出现 `不是 logging 预定义名，已按 INFO 解析`（不再静默）；`LOG_LEVEL=`（空值）降级并警告；提示与警告都落进当天日志文件。

> ⚠️ **需要你就一处措辞过目（第 4 轮披露）**：FR-1 原文写的是"两种情况都必须输出一条明确的 **WARNING**"。实现里那条提示按 `max(生效级别, WARNING)` 发出——生效级别 ≤ WARNING 时逐字就是 WARNING（`info` / `debug` / 空值等常见手误都在这一档），但 `LOG_LEVEL=error` / `critical` 这类手误会以 **ERROR / CRITICAL** 发出。原因是 `_setup_logger()` 刚把 root 门槛设成生效级别，固定用 WARNING 会让这条提示被自己设下的门槛吃掉、在那两档重新变成静默。PRD 已把 FR-1 更正为"明确可见的提示——默认 WARNING，生效级别更高时按生效级别发出"，取舍见 §13 D-14 与 `Final Reconciliation`。**这是措辞精确化，可见性承诺没有削弱**；若你更看重"级别必须是 WARNING"这个字面，请勾选"有差异"，我们改回固定 WARNING（代价是那两档看不见）。
- 复跑该档：`env -u PYTHONPATH LOG_LEVEL=error LOG_DIR=/tmp/k175 LOG_FILE=/tmp/k175/app.log uv run iar agent doctor claude --json`（终端与日文件都应出现那条"不是 logging 预定义名"的提示）
- 查看：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-1-invalid-level.txt"`
- 复跑：`bash .iar/evidence/scripts/rv1_invalid_level.sh`

**你的选择**：☐ 同意"降级 + 警告"，且大小写手误按原意生效 + 出一条归一化提示 ☐ 大小写手误也一律按"无效"降级到 INFO ☐ 大小写手误完全不出声 ☐ 保持硬失败、只把报错文案改可读 ☐ 有差异：＿＿＿

---

## 决策三 · handler 幂等从"root 非空就整体跳过"改成"只跳过重复挂载自己"

**你在拍板的事**：进程里只要已经有别人挂的 handler（uvicorn、测试框架、某个先导入的库），今天 keda 会连自己的文件日志都不挂（静默没有文件日志）。改成按私有标记只跳过/移除 keda 自己的 handler。**如果判错**：当第三方已挂 root handler 时，同一条记录会同时流向对方和 keda 的 handler——这是期望行为，但确实是可见变化，可能被误读成"日志重复了"。

**PRD 原话（§2 决策三）**：
> **决策三：handler 幂等策略从"root 非空就整体跳过"改为"只跳过重复挂载自己"，可以接受吗？** …… **请确认：** 接受"root 非空时仍挂载 keda 的 handler"，还是要求"保持整体跳过、只是把跳过改为显式警告"？**验收：** root 预置第三方 handler 时 keda 的文件 handler 仍存在；连续初始化不重复挂载。

**展开说**：警告不能替代文件日志——一旦 root 被占用，keda 就永久没有文件日志，缺陷并未修好。实现上给 keda 自己挂的两个 handler 打了私有标记 `_keda_handler`，重复初始化时只摘掉带标记的旧 handler，绝不碰第三方 handler。这条"日志会同时进两边"的行为已在 `docs/guides/configuration.md` 的日志特性里写明，避免被当成重复日志。

**证据**
- `.iar/evidence/rv-6-handler-idempotency.txt`：红跑（改动前）在 root 已有第三方 handler 的情况下 `keda_handler_count : 0`、`daily_files_opened : []`；绿跑 `keda_handler_count : 2`、`daily_files_opened : ['app-<今天>.log']`、`third_party_still_attached : True`，再跑一次 setup 后仍是 2（没有叠加）。 另有一段红跑：把"已跟踪 handler 的收尾"从副本里删掉后，重建单例用例会因旧日文件句柄未被关闭而变红。
- 查看：`open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence/rv-6-handler-idempotency.txt"`
- 复跑：`bash .iar/evidence/scripts/rv6_handler_idempotency.sh`

**你的选择**：☐ 同意"root 非空时仍挂载 keda handler" ☐ 保持整体跳过、仅改为显式警告 ☐ 有差异：＿＿＿

---

## 第四项 · §9.1 呈递区过目

**你在拍板的事**：确认人读呈递区给出的东西就是你要看的结果（本次交付没有前端变化，呈递物是真实终端输出文本，没有截图）。

**PRD 原话（§9.1 表头）**：
> | 应该看到的结果 | 呈递物 | 10 秒自检 |

**已交付的呈递物（全部为 worktree 本地文本捕获，不进版本库）**
1. `.iar/evidence/rv-1-invalid-level.txt` —— 坏级别从崩溃到降级警告（红/绿对照）
2. `.iar/evidence/rv-7-daily-file.txt` —— 真实入口下当天文件按约定生成、且确有本次运行日志行
3. `.iar/evidence/rv-2-daily-rotation.txt` —— 跨天两个文件的实际产出与命名
4. `tasks/evidence/P0-BUG-20260930-145323-logging-config-robustness/P0-BUG-20260930-145323-logging-config-robustness.evidence-report.md` —— 八项证据汇总与人审导航

一次打开全部：
```bash
open "/Users/zata/code/keda/.iar-worktrees/issue-175/.iar/evidence" \
  "/Users/zata/code/keda/.iar-worktrees/issue-175/tasks/evidence/P0-BUG-20260930-145323-logging-config-robustness/P0-BUG-20260930-145323-logging-config-robustness.evidence-report.md"
```

**10 秒自检要点**：每份证据文件末尾都有 `ITEM rv-<n> check failures: 0`，且全文没有 `CHECK FAIL` 行；红段（Negative control）都出现在绿段之前。

**你的选择**：☐ 已逐项过目并认可 ☐ 有差异：＿＿＿

---

## 由执行器自验 + 独立 verifier 审查、不逐项展示的部分

`rv-3`（保留天数配置化与日切清理）、`rv-4`（合法级别零变化）、`rv-5`（当日路径单点）、`rv-6` 的自动化用例、`rv-8`（文档与配置注释同步）以及 `just test` / `just lint --full` / 架构守卫 / `mkdocs build --strict`，由 Agent 自验并经独立 verifier 审查；只有失败时才向你呈递。结论汇总在 `evidence-report.md` 与 `verifier-report.md`。

## 本次明确不涉及（无需你确认，仅备查）

不新增第三方依赖；不改前端（`No frontend impact`）；不改数据库 / schema；不改日志格式与默认级别；不统一两套 logger 惯例；不引入结构化 / 关联 / 事件日志；不改 uvicorn 访问日志。
