# 独立复核报告：内置 mcode Agent

对应 PRD：`tasks/archive/P1-FEAT-20261116-120000-mcode-builtin-agent.md`
复核方式：独立 Codex CLI review 会话，检查分支改动、PRD 决策与相关调用点。

## Verdict

**PASS** — 实现符合 PRD 的功能要求与已确认的决策；未发现未解决的代码缺陷。

## Review Findings And Disposition

1. 初次复核指出 `--permission` 不应加入 doctor 的 sandbox/approval 标记，否则 `--permission full` 会被误当成保护措施。该改动已撤销；doctor 对 mcode writable profile 的无沙箱告警保留。
2. 复核指出 mcode 的 `deliberate` / `generate` 使用 `--permission full`，同时声明 `read_only = true`。这符合 PRD §2 决策二与 §FR-3：`read_only` 是 planner / `kc ask` 的约定声明，`read_only_enforced` 保持 `false`；用户已确认接受该行为。检查代码调用点后，停滞监督只接受 `read_only_enforced`，mcode 不会被当作强制只读诊断器。
3. JSONL 公共解码与 `item.completed` 信封逻辑已复用 `structured_json_lines.py`，provider 专属事件仍由各自 renderer 处理。

## Validation Reviewed

- `just test all`: **5331 passed, 1 skipped**。
- `just lint --reuse`: **PASS**。
- `just lint --full`: **PASS**。
- `uv run mkdocs build`: **PASS**（有既存未导航页面与链接锚点提示）。
- 真实 console：全局与仓库层下拉中都观察到 `mcode`；仓库层预设保存及配置写回已验证。

## Remaining Evidence Limitation

PRD 的 rv-7 要求两层下拉截图文件随证据包留存。CUA 本轮展示了真实页面截图，但接口没有提供将图像导出至仓库文件的能力，因此截图留存项仍未完成。该项是验证证据阻塞，不是代码复核失败；PRD 保持 `⏸️ 验证阻塞`，补齐图片后再进入人工验收。
