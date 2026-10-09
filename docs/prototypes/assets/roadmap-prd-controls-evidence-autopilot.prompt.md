# roadmap-prd-controls-evidence-autopilot.png

- 生成工具：OpenAI 内置 ImageGen，`precise-object-edit`
- 生成日期：2026-09-16
- 画布：1536 × 1024 PNG
- 参考图片：既有 Roadmap 页面原型，保留真实产品布局与导航结构
- 保持不变区域：整页侧栏、仓库列表、依赖图、PRD 详情和原有控件；只在 Roadmap 工具栏下方加入 Autopilot 状态条，并轻微强调依赖路径
- 预期变化：展示仓库级自动推进开关、daemon 状态、并发上限、自动合并影响说明
- 本图后续修订见 `roadmap-prd-cicd-auto-repair.prompt.md`；该文件记录基于此图的 CI/CD 状态图编辑
- 图片仍用于概念评审，不是运行时截图或功能验收证据

## 完整提示词

```text
Use case: precise-object-edit
Asset type: high-fidelity internal console UI mockup revision
Input image: Image 1 is the exact existing Roadmap redesign mockup and must remain the visual/layout reference.
Primary request: Add the project’s existing Autopilot capability to the Roadmap page as a clear repository-level on/off control. Preserve the entire existing image, page structure, sidebar, repository panel, dependency graph, selected PRD detail, evidence tabs, colors, typography, and dimensions.

Make only these targeted layout changes:
1. Insert a compact full-width settings/status strip directly below the top Roadmap toolbar and directly above the bordered main Roadmap content. It must align with the Roadmap workspace to the right of the repository panel, not cover the graph or detail pane.
2. The strip uses a subtle pale-blue background, thin blue-gray border, and 8px radius. On the left show a blue toggle in the ON position, label “Autopilot 自动推进”, and green status text “已开启”.
3. Next to it show concise explanatory text: “上游全部完成后，自动解锁并启动下游 PRD”.
4. On the far right show two small status chips: green dot “Daemon 运行中” and neutral text “并发上限 2”.
5. Under the main explanatory text, in smaller muted type, show: “自动补位 · 自动解锁下游 · 通过门禁后自动合并”. Include a tiny info icon next to this line.
6. Keep the existing “全局开始” button. Its meaning remains a one-time manual batch start, visually separate from the Autopilot toggle.
7. In the dependency graph, lightly emphasize the path from the archived upstream node “前端 PRD 路线图” to its downstream nodes using a subtle blue connector glow, suggesting automatic advancement. Do not add animation or extra nodes.

Interaction intent the UI should communicate:
- Toggle is repository-scoped for the currently selected “keda-main”.
- ON means the daemon continuously reconciles the roadmap, fills available slots, and starts eligible downstream PRDs once all upstream dependencies are complete.
- Autopilot also participates in the existing automatic merge fast lane, so the secondary explanation must not omit “通过门禁后自动合并”.
- Daemon state is visible because Autopilot cannot advance work unless daemon is running.

Exact Chinese labels that must remain legible:
“Autopilot 自动推进”
“已开启”
“上游全部完成后，自动解锁并启动下游 PRD”
“自动补位 · 自动解锁下游 · 通过门禁后自动合并”
“Daemon 运行中”
“并发上限 2”
“全局开始”

Constraints: change only the area needed to add the Autopilot strip and subtle connector emphasis. Keep the original 1536×1024 composition. No modal, no floating tooltip, no dark theme, no gradients, no marketing illustrations, no watermark. Do not remove or rename any existing controls. Preserve all evidence content on the right.
```
