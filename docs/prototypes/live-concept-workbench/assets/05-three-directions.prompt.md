# 方向 05：多方向对比

- 工具：OpenAI 内置 ImageGen（`image_gen.imagegen`）
- 日期：2026-11-19
- 模式：文本提示生成，无参考图
- 画布：1586 × 992 像素（约 16:10）
- 用途：探索 Agent 并列呈现多个临时方向、由用户选择后继续收敛；静态设计概念，不可点击。
- 参考图：无

## 完整提示词

~~~text
Use case: ui-mockup
Asset type: static high-fidelity desktop web-app concept screenshot, option 05 of a six-direction visual exploration for KedaCode.
Primary request: A "构想工作台" that proposes three distinct visual directions from an unfinished idea so the user can react and choose. This option emphasizes comparison and creative divergence.
Scene/backdrop: KedaCode Console-inspired light desktop product UI, wide 16:10 screenshot, no device frame.
Subject: Far-left slim pale-lavender KedaCode navigation. The main workspace has a broad left visual board with three large alternative concept cards arranged side by side, each clearly tagged "方向 A", "方向 B", "方向 C", with a distinct miniature preview: A a polished generated website hero, B a connected idea map, C an illustrated four-step storyboard. All cards relate to the marked illustrative idea "邻里工具共享（示意内容）". Show one selected card with a fine violet outline and an understated action "继续打磨". The right conversation panel contains a short message: the AI has created three directions and asks which feels closer. Top bar says "构想工作台" with "保存到 Ideas".
Style/medium: coherent premium SaaS UI, light neutral surfaces, KedaCode violet primary accent, small teal details, strong whitespace, readable Chinese, clean realistic desktop web app.
Composition/framing: 16:10 full interface, navigation at left, three compare-and-select cards across the central 65%, chat panel on right 30%; show clear hierarchy and generous spacing.
Text (verbatim): Use short accurate labels only: "构想工作台", "三种初步方向 · 示意内容", "邻里工具共享", "方向 A · 网页", "方向 B · 流程图", "方向 C · 故事板", "继续打磨", "保存到 Ideas", "我先按你的描述试着展开三种方向。", "哪一个更接近你想表达的？", "我更喜欢…", "发送".
Constraints: Example idea and all generated concepts are illustrative only; the user has not approved any direction. Clearly present alternatives as provisional.
Avoid: fake product metrics, claiming decisions are final, code editor, dark theme, random dashboards, dense tiny labels, multiple separate screenshots, watermark, cropped interface.
~~~
