# roadmap-prd-controls-evidence-autopilot-progress-running.png

- 生成工具：OpenAI 内置 ImageGen，`precise-object-edit`
- 生成日期：2026-10-10
- 画布：1536 × 1024 PNG
- 参考图片：既有 Roadmap 页面原型，保留真实产品布局与导航结构
- 用途：交付进度信号（P1-FEAT-20261010-093054）「运行中」关键状态目标图，`design intent`
- 状态：PRD 正在执行，读数来自真实工作区；同名文件带「未合并」徽标
- 本图与真实 console 运行中截图配对呈递；它是概念目标图，不是运行时截图或功能验收证据

## 完整提示词

```text
Use case: precise-object-edit
Asset type: high-fidelity internal console UI mockup (Keda "iar" admin console, light theme, white sidebar, shadcn/Tailwind components, Chinese labels).
Scene: A PRD detail panel in the "验收证据" (acceptance evidence) tab, shown for a PRD that is CURRENTLY RUNNING. Preserve a realistic enterprise dashboard layout: left navigation sidebar, top toolbar, and a master-detail pane.

Header of the detail panel shows three compact weak-signal reading chips in a row, each with a small gray label and a rounded badge:
- "清单 3/12" (neutral gray badge)
- "影响树 ~7/12 ?2" (sky-blue badge; the tilde and question mark must be visible)
- "证据 2/3" (sky-blue badge)
A tiny info hint next to the row reads "弱信号：不代表验证通过".

Main evidence panel:
- A summary line: "计划 3 项 · 已产出 2 项（共 4 个文件）"
- A second line: "报告三件套：验证计划 已产出 · 证据报告 未产出 · 独立验证 未产出"
- Files grouped by validation item:
  - "rv-1 运行中详情显示交付读数 [人验]" group header, with two file rows. Each file row has an amber "未合并" badge (worktree source, not merged), a filename like "rv-1-running-detail.png" and "rv-1-verification-plan.md", a size, and 预览/下载 buttons.
  - "rv-2 读取边界与工作区优先级正确 [机验]" group header with a muted placeholder "待产出" and no files.
  - "rv-3 影响树读数与 CLI 一致 [机验]" group header with one file row "rv-3-cli-parity.txt" and an amber "未合并" badge.
  - A collapsed "其他（报告 / 附件 / 计划外文件）" group.

Constraints: light theme, no dark mode, no gradients, no marketing illustration, no watermark. Keep all Chinese text crisp and legible. Clean flat enterprise SaaS styling. Exact aspect ratio 1536x1024.
```
