# Verification Plan: agent CLI 适配层（统一命令构造器 + 声明式注册表）

对应 PRD：`tasks/archive/P1-FEAT-20260911-010513-agent-cli-adapter-layer.md`（§7.6 为权威计划，本文件为执行摘要）

## 覆盖口径

| rv | 内容 | 层级 | 入口 |
|---|---|---|---|
| rv-1 | 12 条既有命令行逐字节零变化（黄金快照 diff）；旧 `[agent_runner.labels]` 配置兼容 | R1 | 一次性脚本（旧树）vs `iar agent doctor --json`（新树）|
| rv-2 | pi 只读真实运行 + 负控（红/绿两份） | R1·manual | `iar ask --agent pi --plan-only` |
| rv-3 | 字面量不展开；沙箱缺失告警；坏输入 fail-fast | R1 | `IAR_CONFIG` 注入临时配置 + 假 bin，doctor/真实 CLI |
| rv-4 | `iar deliberate --agents pi` 真实辩论轮 | R1·manual | `uv run iar deliberate ... --agents pi` |
| rv-5 | `iar agent list` / `doctor pi` 绿；三类坏输入红 | R1 | 真实 CLI |
| rv-6 | 旧构造点符号零残留；agent 名字面量收敛 24 → 可枚举 | R1·smoke | `rg` 搜索断言 |
| rv-7 | 全量回归 + 质量门禁 | R0 | `just test all` / `just lint --full` / `mkdocs build --strict` |

## 关键 provenance

- **临界值来源**：黄金哨兵 `golden-prompt`（doctor 默认值 = 导出脚本常量，不含空格）。
- **跨越的边界**：`build_agent_invocation`（core 纯函数）→ doctor 输出（api 真实入口）→ 假 agent 子进程 `sys.argv`（真实进程边界），三段必须逐字节一致。
- **被禁的旁路**：不接受"改快照迁就实现"；before 快照必须在改造前旧树采集；负控红色不得靠给生产代码加故障注入开关制造。
- **fresh-state 探针**：`IAR_CONFIG` 指向 `mktemp -d` 临时配置 + `mktemp` 假 bin，不污染本仓 `config.toml`。
- **final-tree 重采时点**：最后一次代码改动（`output_protocols/__init__.py` F401 修复）之后重采 after 快照与 rv-3/rv-5 探针；其后仅文档变更。

## 豁免预授权（来源：PRD §12 风险表）

rv-2 / rv-4 依赖本机已安装并认证的 pi，标记 `manual` / opt-in；无 pi 环境的降级路径（假 agent 脚本走同一代码路径）不计入这两条的验收证据。本轮未执行，按豁免遗留人工确认。
