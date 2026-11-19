# 方向 01：网页实时预览

- 工具：OpenAI 内置 ImageGen（`image_gen.imagegen`）
- 日期：2026-11-19
- 模式：文本提示生成，无参考图
- 画布：1586 × 992 像素（约 16:10）
- 用途：探索对话旁直接呈现生成网页的布局；静态设计概念，不可点击。
- 参考图：无

## 完整提示词

~~~text
Use case: ui-mockup
Asset type: static high-fidelity desktop web-app concept screenshot, option 01 of a six-direction visual exploration for KedaCode.
Primary request: A polished Chinese UI concept for "构想工作台": the user chats with an AI on the right while a rough idea becomes a live website preview on the left. This option emphasizes seeing an actual generated web page.
Scene/backdrop: A realistic desktop web application inside the existing KedaCode Console style; wide 16:10 screenshot, full bleed, no device frame.
Subject: A light, clean application shell with a narrow pale-lavender KedaCode navigation sidebar, then a main workspace split about 64/36. The left is a browser-like preview canvas with a subtle address bar and an elegant illustrative preview website for a clearly labeled example idea, "邻里工具共享（示意内容）": a warm hero section, a simple tool-card row, and a primary button. The right is a calm conversational panel with two short messages and a composer, showing the agent has just updated the preview. A compact top bar says "构想工作台", with a small "保存到 Ideas" action and a streaming status dot.
Style/medium: crisp shadcn-like product UI, realistic frontend mockup, clean editorial hierarchy, restrained violet primary accent matching KedaCode, muted cyan secondary accents, soft neutral surfaces, subtle borders, excellent spacing and legibility.
Composition/framing: entire desktop app visible at 16:10; KedaCode navigation on far left; large website preview dominates center; AI conversation occupies right; clear separation and no overlap.
Text (verbatim): Use only short, correctly spelled Chinese labels: "构想工作台", "预览", "对话", "保存到 Ideas", "邻里工具共享（示意内容）", "借到需要的工具，也把闲置工具分享出去", "浏览工具", "我想让附近的人更方便地借到闲置工具。", "我先把这个想法做成一个可预览的页面。", "继续描述，预览会随讨论更新…", "发送".
Constraints: static visual concept only; preserve the recognizable split-pane workflow; no real product data or claims; no fabricated production status; no fake OS chrome; high readability.
Avoid: multiple screens in one image, diagrams, busy dashboard metrics, dark theme, photorealistic people, code editor, watermark, gibberish text, cropped UI.
~~~
