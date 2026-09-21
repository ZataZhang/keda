# PRD 生命周期观测与执行分析原型

这是一个 **interactive prototype**，用于评审 `frontend-public` 中 Roadmap 与 Stats 的目标交互，不代表生产功能已经实现或已接入真实 API。

## 启动与入口

- 文档站入口：`docs/prototypes/hub.html`
- 直接入口：`docs/prototypes/prd-lifecycle-observability.html`
- 本地预览：`uv run mkdocs serve`，然后打开 `http://127.0.0.1:8000/prototypes/hub.html`

## 状态模型

```text
Hub
└── Roadmap / PRD 已选中 / 执行过程
    ├── 点击事件 → 事件详情抽屉 → 关闭
    ├── 正常轨迹 ⇄ 失败与重试
    └── 查看统计 → PRD 执行统计 → 点击 PRD 返回生命周期详情
```

## 关键点击语义

- Hub 中点击缩略图或名称：直接打开原型；点击行内其他区域：在 Hub 右侧打开详情。
- Roadmap 中“查看统计”和侧栏“统计”：切换到 PRD 生命周期统计。
- 时间线任一事件：打开事件详情抽屉；关闭按钮或 `Esc` 返回。
- “正常轨迹 / 失败与重试”：切换完整成功路径与保留失败历史的路径。
- Stats 表格中的“PRD 生命周期观测”：返回对应 PRD 详情。
- 底部原型 Dock 提供 Hub、说明、可点击区域和重置入口；它不属于产品界面。

## 模拟范围

- 模拟 `RoadmapPrd`、生命周期事件、attempt、阶段耗时和仓库统计聚合后的目标展示。
- 统计数字与事件 ID 均为稳定 fixture，不暗示已存在生产 API。
- 未实现真实日志流、数据刷新、分页、搜索、权限、错误请求和跨仓库切换。

## 设计依据

- 产品 shell、色彩与排版参考 `frontend-public/app/(app)/`、`frontend-public/app/globals.css` 和现有 Roadmap/Stats 页面。
- PRD 详情标签沿用 `frontend-public/components/roadmap/prd-detail.tsx` 的“PRD 原文 / 验收证据”结构，并新增“执行过程”。
- Stats 入口沿用 `frontend-public/app/(app)/app/stats/page.tsx`，将统计口径改为完整 PRD 生命周期。
- 本原型完全由 HTML/CSS/JavaScript 构建，无 AI 生成图片，因此不需要 `.prompt.md`；代码与本说明即来源记录。

## 生产实现仍需确认

- 生命周期事件的持久化 schema、旧运行记录迁移与数据保留周期。
- “执行时间 / 等待时间 / 阻塞时间”的精确定义及跨时区聚合。
- 当前 Issue 与 PRD 路径无法关联时的降级展示。
