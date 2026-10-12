# 时间显示与时区

Issue #239 确立的跨入口约定：**记录与机器输出保持 UTC 语义，人读展示按用户所在机器的本地时区**。新增或改动任何时间展示路径前先对照本页。

## 分层契约

| 层 | 时区 | 说明 |
|---|---|---|
| 数据库 / API JSON / CLI JSON 等机器输出 | UTC，带 `+00:00` 偏移（兼容 `Z`） | 由 `datetime.now(timezone.utc).isoformat(timespec="seconds")` 产生；展示层改动不得触碰这些原文 |
| Web（frontend-public / frontend-admin）人读时间标签 | 浏览器当前时区 | 只渲染真实瞬间（带偏移的 ISO 字符串）；转换发生在客户端 |
| CLI 人读输出（表格、提示文案） | 执行机器本地时区 | 用 `backend.api.cli_utils.format_local_display_time`；JSON 分支不得经过该函数 |

不硬编码任何具体时区（如 `Asia/Shanghai`）；也不新增时区设置页或用户配置。

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

- 夏令时回拨导致同一本地小时出现两次是**合法行为**：展示必须保留偏移（CLI 本地文本带 `±HH:MM`），以便区分两个真实瞬间；不要求消除合法重复小时。
- 空值显示占位符（`—`），非法值原样回显；跨日由本地字段换算自然处理。
- 耗时、调度、聚合计算（如按 UTC 日历日的趋势分桶）属于记录侧计算，不因展示改动而变；分桶语义如需按本地日聚合，属于新需求另立 PRD。

## 验证入口

- CLI：`TZ` 环境变量覆盖（UTC / Asia/Shanghai / 一个夏令时区），断言人读表格与 JSON 输出各按契约显示；见 `tests/test_cli_time_display.py`。
- Web：同一瞬间在浏览器改时区核对时间标签与「原始时间」对照行；见 `tests/playwright-e2e/`。
