# Roadmap 单 PRD 控制、验收证据与 Autopilot 草图

本页归档 `tasks/pending/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot.md` 的视觉草图与 ImageGen 提示词。草图用于确认信息架构和交互含义，不是像素级实现规范；正式实现继续复用 `frontend-public` 当前的 shadcn/Tailwind 组件与布局。

从 [Prototype Hub](hub.html) 可继续打开相关的 CI/CD 监控与 PRD 生命周期原型；Hub 的关系入口由 registry 维护。

## 最终草图

![Roadmap 单 PRD 控制、验收证据与 Autopilot 最终草图](assets/roadmap-prd-controls-evidence-autopilot.png)

最终稿保留真实项目的白色 `iar` 侧栏、受管理仓库列表、紧凑依赖节点和同页 master-detail 详情，并增加仓库级 `Autopilot 自动推进` 开关、daemon 状态、并发上限、自动合并影响说明与验收证据入口。

## 生成方式

- 工具：OpenAI 内置 ImageGen
- 用例分类：`precise-object-edit`
- 画布：1536 × 1024 PNG

## 最终编辑提示词

最终编辑提示词全文保存在同名旁车文件 [`assets/roadmap-prd-controls-evidence-autopilot.prompt.md`](assets/roadmap-prd-controls-evidence-autopilot.prompt.md)，本页只保留设计说明，避免图片与提示词记录分离。

## 实现解释

图中“通过门禁后自动合并”是现有能力的影响说明，不表示这个 Roadmap 开关会同时修改 `safety.auto_merge`。正式实现按 PRD 决策：开关只修改当前仓库的 `autopilot.enabled`，并单独展示 daemon 与自动合并门禁状态。
