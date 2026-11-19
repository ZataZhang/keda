# 方向 02：概念关系画布

- 工具：OpenAI 内置 ImageGen（`image_gen.imagegen`）
- 日期：2026-11-19
- 模式：文本提示生成，无参考图
- 画布：1586 × 992 像素（约 16:10）
- 用途：探索以关系画布呈现用户、价值、流程与待确认项；静态设计概念，不可点击。
- 参考图：无

## 完整提示词

~~~text
Use case: ui-mockup
Asset type: static high-fidelity desktop web-app concept screenshot, option 02 of a six-direction visual exploration for KedaCode.
Primary request: A Chinese "构想工作台" screen for early idea-shaping. This option emphasizes a flexible visual concept map instead of a generated website preview.
Scene/backdrop: Existing KedaCode Console-inspired light application shell, wide 16:10 desktop screenshot, no device frame.
Subject: A slim pale-lavender KedaCode sidebar on the far left. In the workspace, a generous whiteboard canvas occupies roughly two-thirds and a conversational AI panel sits on the right. On the canvas, show a refined connected-node map about the explicitly illustrative idea "邻里工具共享（示意内容）": central idea card connected by thin violet lines to four clearly named cards "用户场景", "核心价值", "使用流程", "待确认". Use tasteful tiny icons and one small illustrative thumbnail, with lots of breathing room. The chat panel contains a short user message and an assistant follow-up. Top bar: "构想画布", "保存到 Ideas", and subtle zoom controls.
Style/medium: polished SaaS canvas, shadcn-like surfaces, quiet neutral background, violet primary accents and a few cyan/green states, crisp readable Chinese UI, thin borders, very light shadows.
Composition/framing: full interface at 16:10; preserve three distinct zones: navigation, large open canvas, right chat. Canvas nodes should be the visual focus; chat remains easy to scan.
Text (verbatim): Use only short legible Chinese labels: "构想画布", "邻里工具共享（示意内容）", "用户场景", "核心价值", "使用流程", "待确认", "概念仍在收敛", "我想先把这个想法拆成几个部分，边聊边补全。", "附近的人想借到偶尔才用的工具", "共享闲置工具，减少重复购买", "发布工具 → 预约 → 取还", "如何处理损坏或逾期？", "保存到 Ideas", "继续描述想法…", "发送".
Constraints: This is a static design concept, not a production screenshot; indicate sample idea is illustrative; no fake analytics or established business policies.
Avoid: browser chrome, code editor, busy dashboard metrics, dark theme, dense illegible text, people, watermark, multiple screens, cropped controls.
~~~
