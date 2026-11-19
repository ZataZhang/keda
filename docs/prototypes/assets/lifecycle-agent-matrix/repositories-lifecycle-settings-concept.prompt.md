# repositories-lifecycle-settings-concept.png

- 类型：AI 生成的视觉概念图
- 生成工具：内置 ImageGen
- 生成日期：2026-11-13
- 画布：1555 × 1012
- 参考图片：references/settings-entry-reference.png（现有 Settings 统一设置入口）；references/repositories-shell-reference.png（KedaCode Runner 页面与 Repositories 导航）
- 前一状态：无；使用两张参考图独立生成
- 预期验证层级：设计概念。图中生成的阶段名称、Agent、模型和描述只作排版示意，不是产品配置或验证证据。

## 保持不变

- 沿用 KedaCode 的浅色后台外壳、侧边栏、紫色强调色、字体层级和卡片风格。
- 统一设置页保留全局与仓库两个配置范围；仓库范围再选择具体仓库。
- 生命周期、预设管理和回退候选仍属于同一个统一设置页面。

## 本次变化

- 在 Repositories 顶层增加与“仓库列表”并列的“执行器与模型”标签。
- 将统一设置页面放在该顶层标签下，不要求先打开某个仓库详情。
- 不复现参考图中的红色箭头。

## 完整提示词

Use case: ui-mockup
Asset type: high-fidelity desktop web app screenshot, corrected concept 1 of 5
Primary request: Place the existing unified lifecycle settings at the top level of the Repositories area, as a page sibling to the repository list. This is GLOBAL/REPOSITORY configuration, not settings inside one repository.
Input images: Reference image 1 is the current settings card that should be relocated. Reference image 2 is the KedaCode dashboard shell; its red arrow only indicates the Repositories navigation and must not appear.
Subject: KedaCode shell with left navigation and “Repositories” active. Main page has two clear top-level tabs “仓库列表” and “执行器与模型”, with “执行器与模型” active. Show the relocated page titled “生命周期、模型与推理深度统一设置”, selector “配置范围” with “全局” and “仓库” options, and a repository dropdown available when “仓库” is selected. Show a readable nine-stage table with columns 阶段、Agent、模型、推理深度、来源, plus lower sections “预设管理” and “回退候选”. This config page must work without opening any repository detail.
Style/medium: realistic, polished KedaCode product screenshot; match its existing pale neutral shell, purple accent, typography, card edges and spacing.
Composition/framing: wide desktop view, full sidebar and configuration canvas.
Constraints: keep the page inside Repositories top-level, retain global/repository scope switching, do not show a single-repository detail page, no red arrow, no watermark.
