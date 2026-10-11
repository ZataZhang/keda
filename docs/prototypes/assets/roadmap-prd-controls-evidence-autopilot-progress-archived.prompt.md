# roadmap-prd-controls-evidence-autopilot-progress-archived.png

- 生成工具：OpenAI 内置 ImageGen，`precise-object-edit`
- 生成日期：2026-10-10
- 画布：1536 × 1024 PNG
- 参考图片：既有 Roadmap 页面原型，保留真实产品布局与导航结构
- 用途：交付进度信号（P1-FEAT-20261010-093054）「已归档」终态关键状态目标图，`design intent`
- 状态：主仓库已归档副本是终态事实来源，忽略遗留工作区；证据来源回落主仓库、不显示「未合并」；影响树为 `-`
- 本图与真实 console 归档截图配对呈递；它是概念目标图，不是运行时截图或功能验收证据

## 完整提示词

```text
Use case: precise-object-edit
Asset type: high-fidelity internal console UI mockup (Keda "iar" admin console, light theme, white sidebar, shadcn/Tailwind components, Chinese labels).
Scene: A PRD detail panel in the "验收证据" tab for a PRD that is ALREADY ARCHIVED (terminal state, read from the main repository, not a worktree).

Header shows three weak-signal reading chips:
- "清单 12/12" (green-ish completed badge)
- "影响树 —" (neutral gray badge showing a single em-dash, meaning not comparable because archived)
- "证据 3/3" (badge)
A small muted note beside the impact chip reads "归档终态：不参与分支比较".

Main evidence panel:
- Summary line: "计划 3 项 · 已产出 3 项（共 5 个文件）"
- Files grouped by validation item "rv-1" / "rv-2" / "rv-3", each with file rows. IMPORTANT: NO "未合并" badges anywhere — all files come from the main repository (source = 主仓库). Each file row shows a plain filename, size, and 预览/下载 buttons.
- No amber worktree badges at all.

Style difference from a running PRD: this panel communicates terminal/merged state — completed checklist, dashes for impact, plain main-repo evidence with no un-merged markers.

Constraints: light theme, no dark mode, no gradients, no watermark, crisp legible Chinese text, flat enterprise SaaS styling, aspect ratio 1536x1024.
```
