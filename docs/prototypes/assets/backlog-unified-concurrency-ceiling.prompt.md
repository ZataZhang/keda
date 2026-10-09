# backlog-unified-concurrency-ceiling 生成提示词旁车

- 图片：[`backlog-unified-concurrency-ceiling.png`](backlog-unified-concurrency-ceiling.png)
- 工具：ImageGen（`precise-object-edit` 风格的目标态 UI 草图）
- 画布：1536 × 1024 PNG
- 来源 PRD：`tasks/pending/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling.md`
- 预期变化：Backlog 顶部控制条把「并发」从一次性启动参数升级为**持久策略 + 生效上限三态显示**（继承 / Backlog 设置 / 受 runner 容量限制），并提供 1–10 输入与「恢复继承」。

## 完整提示词

> A clean, realistic screenshot-style web UI mockup of a Chinese-language developer dashboard page called "Backlog", light theme, white background, flat shadcn/Tailwind style, thin gray borders, small rounded corners, no photorealism. Left edge: narrow white sidebar with a small black logo text "iar" and a vertical list of gray menu items, one item highlighted pale blue. Main content top: page title "Backlog" and a repository selector chip "keda-main". Below it, one horizontal control bar in a bordered card, containing left to right: a checked blue checkbox with label "Backlog 自动推进"; green dot text "● Daemon 运行中"; amber text "自动合并未启用"; then a separator dot and a highlighted concurrency group with a subtle blue-tinted background box containing: label "并发 2（Backlog 设置）", a small numeric input showing "2" with up/down spinner and tiny hint "1–10" below it, a small dark primary button "保存", and a small outline button "恢复继承". Under the control bar a second thin gray caption line: "生效上限 = min（Backlog 并发策略, runner 容量）· 容量 4". Below, a compact dependency graph area: five small rounded rectangle nodes labeled "P1-FEAT…" connected by thin arrows, two nodes with a tiny blue "运行中" tag, one node green "待审阅", arranged in a loose left-to-right DAG, mostly whitespace. Crisp small sans-serif Chinese text, accurate characters, UI screenshot aesthetic, 1536x1024.

## 图中关键文案

- “Backlog 自动推进”
- “● Daemon 运行中”
- “并发 2（Backlog 设置）”
- “保存” / “恢复继承”
- “生效上限 = min（Backlog 并发策略, runner 容量）· 容量 4”

## 与实现的对应关系

草图只确认信息架构：数字「2」来自策略值，括号里的来源标签由 `ceiling_source` 三态渲染；真实实现的验收截图见
`tasks/evidence/P1-FEAT-20261010-011714-unified-auto-concurrency-ceiling/`（三态各一张：继承 / 策略 / 受容量限制）。
