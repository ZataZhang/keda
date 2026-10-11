# roadmap-prd-controls-evidence-autopilot-progress-degraded.png

- 生成工具：OpenAI 内置 ImageGen，`precise-object-edit`
- 生成日期：2026-10-10
- 画布：1536 × 1024 PNG
- 参考图片：既有 Roadmap 页面原型，保留真实产品布局与导航结构
- 用途：交付进度信号（P1-FEAT-20261010-093054）「无计划 / 降级」关键状态目标图，`design intent`
- 状态：无验证计划、无工作区或无有效比较基准时，三个读数一律 `-`（不伪造零、不显示 `~0/y`），文件列表仍如实反映磁盘真实文件；同一降级样式亦用于「无基准」状态
- 本图与真实 console 无计划 / 无基准截图配对呈递；它是概念目标图，不是运行时截图或功能验收证据

## 完整提示词

```text
Use case: precise-object-edit
Asset type: high-fidelity internal console UI mockup (Keda "iar" admin console, light theme, white sidebar, shadcn/Tailwind components, Chinese labels).
Scene: A PRD detail panel in the "验收证据" tab for an OLD PRD that has NO validation plan, NO worktree, and NO valid merge-base — a graceful DEGRADED state.

Header shows three weak-signal reading chips, ALL displaying a single em-dash (not a fabricated zero):
- "清单 —" (neutral gray badge, em-dash only)
- "影响树 —" (neutral gray badge, em-dash only)
- "证据 —" (neutral gray badge, em-dash only)
A tiny hint reads "不可比较 / 无计划：显示 —，不伪造数字".

Main evidence panel:
- A summary line reading "该 PRD 未声明验证计划" in muted gray.
- NO per-rv group headers, NO fabricated "计划 0 项" — instead a simple flat file list is shown honestly: header "共 2 个文件", two file rows: "evidence-report.md" and "verification-plan.md", each with size and 预览/下载 buttons. A line "报告三件套：验证计划 已产出 · 证据报告 已产出 · 独立验证 未产出".
- A footnote: "已查找位置：tasks/evidence/…".

Key message: when there is no plan / no worktree / no base, every reading is a dash and the file list still reflects real files on disk — nothing is faked as zero.

Constraints: light theme, no dark mode, no gradients, no watermark, crisp legible Chinese text, flat enterprise SaaS styling, aspect ratio 1536x1024.
```
