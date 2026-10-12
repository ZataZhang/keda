# 时间显示与时区

Issue #239 确立的跨入口约定：**记录与机器输出保持 UTC 语义，人读展示按用户所在机器的本地时区**。新增或改动任何时间展示路径前先对照本页。

## 分层契约

| 层 | 时区 | 说明 |
|---|---|---|
| 数据库 / API JSON / CLI JSON 等机器输出 | UTC，带 `+00:00` 偏移（兼容 `Z`） | 由 `datetime.now(timezone.utc).isoformat(timespec="seconds")` 产生；展示层改动不得触碰这些原文 |
| Web（frontend-public / frontend-admin）人读时间标签 | 浏览器当前时区 | 只渲染真实瞬间（带偏移的 ISO 字符串）；转换发生在客户端 |
| CLI 人读输出（表格、提示文案） | 执行机器本地时区 | 用 `backend.api.cli_utils.format_local_display_time`；JSON 分支不得经过该函数 |
| 面向未知读者的发布产物（GitHub 评论、Issue 正文） | UTC + 显式标注 | 见「发布产物的裁决」 |

不硬编码任何具体时区（如 `Asia/Shanghai`）；也不新增时区设置页或用户配置。

「CLI 人读输出」指 `kc` 命令本身（`src/backend/api/`）。`just` 工具脚本
（`scripts/shared/just/*.py`）**不在本约定的强制范围内**：它们由 bare `python3` 运行
（见 `justfile.shared` 的 `python3 ./scripts/shared/just/prd_lock.py …`），导入后端
helper 会在未安装本包的机器上直接失败，而把换算逻辑复制进脚本又违反
`docs/ai-standards/code-reuse.md`。这些脚本的终端提示（如 PRD 领锁冲突里的
`started_at` / `heartbeat_at`）因此按机器契约原样打印 UTC 带偏移文本：时间是**无歧义的真实瞬间**，
只是未按阅读者本地时区换算。需要本地化时，先把换算 helper 下沉到脚本可共享的层，再改这两处。

## 发布产物的裁决

runner 生成并**发布给未知读者**的文本保留 UTC 并在字段名上显式标注，生成时不猜测任何人
的时区，发布后也不回填换算：

- `core/use_cases/agent_runner_failure.py` 的尝试历史表（`Started (UTC)`）。
- `core/use_cases/agent_runner_stale_attempt.py` 的中断现场表（`Interrupted at (UTC)`）。

这与 Issue #239 豁免「已发布的历史 GitHub 评论」同源：评论一旦发布就面向所有时区的读者，
本地化只会把「谁的本地时间」写成歧义。新增此类发布产物时，照此在列名或字段名里带
`(UTC)`，不要当作清点遗漏去改；面向单个浏览器/终端读者的界面才走本地换算。

## 无时区值的处理

账本里存在**墙钟标签**（wall-clock label）：它们记录某台机器本地的墙上时间，本身不代表可换算的真实瞬间，规则是**原样展示、不猜测、不补偏移**：

- `ideas.md` 头部时间戳按 `docs/guides/idea-inbox.md` 约定是捕获机器本地墙钟；`/ideas` 页面原样展示。产品写入路径（`core/use_cases/idea_inbox.py::append_idea` 默认值）遵循同一本地墙钟约定，不得写 UTC 墙钟。
- 只有当来源契约确认是 UTC（如账本 `now_iso`）时，才允许把缺 `T`/缺偏移的历史值按 UTC 解析；不要对所有无时区字符串一律补 `Z`。

## 实时日志前缀的转换能力限制

项目生成的流式日志行前缀是 `[HH:MM:SS]`（`core/shared/interfaces/output_timestamps.py`），由 runner 执行机器的本地时钟在写入时盖戳，**不含日期与时区**。它不是可换算的真实瞬间：

- Web 日志视图（Issue 实时输出、进程日志抽屉）原样展示，不得把 runner 机器的本地时间当成浏览器时区去转换或标注；UI 已带说明文案。
- 终端与 `kc logs` 通常运行在同一台机器，前缀墙钟与本机时钟一致。
- 需要跨机器对时锚点时，读事件账本里的完整 ISO 时间（`occurred_at` 等），而不是解析日志行前缀。

## Web 侧实现

- frontend-public 复用 `lib/utils.ts::formatLocalDateTime` / `formatLocalClockTime`（内部用 `Date` 的本地字段，正确处理跨日与夏令时）；已正确本地化的显示不强制重构。
- frontend-admin 复用现有 `toLocaleString` / `date-fns format` 本地渲染。
- 时间文本必须在客户端数据到达后渲染（页面为 client component 且数据在 effect 中拉取），避免 SSR 与服务端时区造成 hydration 差异。

## 夏令时与边界

- 夏令时回拨导致同一本地小时出现两次是**合法行为**：CLI 本地文本带 `±HH:MM` 偏移，可区分两个真实瞬间；Web 标签（`formatLocalDateTime` 产出的 `yyyy-MM-dd HH:mm:ss`）不带偏移，回拨窗口内的两个瞬间会渲染成同一串墙钟——这是预期结果，不得为消除重复小时去猜测来源或改写时间源。
- 空值显示占位符（`—`），非法值原样回显；跨日由本地字段换算自然处理。
- 耗时、调度、聚合计算（如按 UTC 日历日的趋势分桶）属于记录侧计算，不因展示改动而变；分桶语义如需按本地日聚合，属于新需求另立 PRD。

## 验证入口

- CLI：`TZ` 环境变量覆盖（UTC / Asia/Shanghai / 一个夏令时区），断言人读表格与 JSON 输出各按契约显示；见 `tests/test_cli_time_display.py`（含 loop / lifecycle / daemon 三个命令的 `--json` 分支仍输出 UTC 原文的断言）。
- Web：时间标签复用 `formatLocalDateTime` / `toLocaleString` 的客户端本地渲染（见「Web 侧实现」），日志视图带墙钟说明文案。真实入口的三时区对照见 `tests/playwright-e2e/tests/workflows/time-display-browser-timezone.no-auth.spec.ts`：同一次运行里用 Playwright `timezoneId` 分别在 UTC / Asia/Shanghai / America/New_York 打开 `/app/backlog`，核对同一瞬间的标签、跨日进位、回拨造成的合法重复小时，并断言无 hydration 报错。该用例走真实浏览器与真实路由，只把 Backlog 读端点与本地会话替换成确定性 fixture（源瞬间必须钉死才能三时区对照），验证层级写在 spec 头部注释里。
