# roadmap-prd-cicd-auto-repair.png

- 生成工具：本地精确像素修改（Python + Pillow 脚本，按区域重绘）；基底图由 OpenAI 内置 ImageGen 生成
- 生成日期：2026-09-16
- 画布：1536 × 1024 PNG
- 参考图片：`roadmap-prd-controls-evidence-autopilot.png`（基底图，同目录）
- 上一状态：基底图，选中态在已归档的「前端 PRD 路线图」上

## 保持不变

- 左侧 `iar` 侧栏与受管理仓库列表、顶部工具条、顶部仓库控制条与 daemon 状态
- 依赖图中「控制台 Dashboard 快照同步」「归档证据浏览」两个节点与全部连线形状
- 右侧详情的「此 PRD 的 CI/CD 自动修复」开关卡片、`跟随全局 / 强制开启 / 强制关闭` 三态控件、`当前生效：关闭`、轮次摘要、`失败` 徽章、`查看 GitHub 检查` 与「立即修复此问题」按钮
- 画布尺寸、留白与整体配色

## 本次变化

| 区域 | 像素范围（x, y） | 变化 |
|---|---|---|
| 依赖图两个节点 | 459..799, 505..666 | 蓝色描边 + 淡蓝底色从「前端 PRD 路线图／已归档」移到「Agent Runner 会话持久化／运行中」；前者恢复白底 + 浅灰描边 |
| 详情标题与「等待 CI/CD」胶囊 | 1021..1364, 215..249 | 标题改为「Agent Runner 会话持久化」；胶囊顺移到新标题右侧 |
| 路径行 | 1022..1493, 266..283 | 改为 `tasks/pending/P1-FEAT-20260916-161500-agent-runner-session.md` |
| 元信息行 | 1025..1377, 295..313 | 改为 `P1 · Issue #91 · 验收 2/8 · 更新于 2026-09-16` |
| 标签页计数徽章 | 1204..1220, 475..492 | `验收证据 6` → `验收证据 2`（浅蓝圆点徽章按原形状补底） |
| 失败检查项名称 | 1039..1258, 699..720 | `frontend-public / typecheck` → `backend / typecheck` |
| 问题说明文字 | 1037..1455, 767..786 | 改为 `mypy 类型检查失败：SessionSnapshot 缺少 resume_token 字段` |
| 代码行 | 1051..1486, 806..832 | 改为 `'SessionSnapshot' has no attribute 'resume_token'` |

除上表区域外，其余像素与原图逐位一致（已按行带核对 diff）。

## 完整提示词

最终图**没有生成提示词**：它由本地脚本按上表区域重绘——底色与描边从基底图取样、文案按基底图字体与基线重排，因而不存在可用于复现的模型提示词。本轮为同一改动向模型发出过两条提示词，均未采纳，原文如下。

### 1. OpenAI 内置 ImageGen（image-to-image，未采纳）

- 工具 / 模式：OpenAI 内置 ImageGen，`precise-object-edit`；日期：2026-09-16；画布：1536 × 1024
- 结果：输出 1216 × 832，整图重绘（布局位移、节点增减、中文标签大面积伪字形），选中态未按指示移动

```text
Use case: precise-object-edit
Asset type: high-fidelity internal console UI mockup revision (Roadmap PRD 完成后 CI/CD 监控与自动修复)
Input image: Image 1 is the exact existing Roadmap mockup and must remain the visual/layout reference. Preserve the whole image: left white "iar" sidebar, managed repository list, repository toolbar, top settings strip, dependency graph, right detail pane, all colors, typography, spacing, shadows and the 1536x1024 composition.

Make only these targeted changes:

A. Dependency graph (selection must follow the running PRD)
1. Remove the blue selection style from the left node "前端 PRD 路线图": it currently has a thick blue rounded border plus pale blue tint. Render it exactly like the other plain nodes: white fill, thin light-gray rounded border, same two-line-free single-line title text "前端 PRD 路线图" and the same green pill badge "已归档" in the same position.
2. Apply that same thick blue rounded border and pale blue tint to the node "Agent Runner 会话持久化" (middle column, lower row, currently plain white with a light-gray border). Keep its two-line title "Agent Runner 会话持久化" and its blue pill badge "运行中" unchanged and fully legible.
3. Keep all curved blue dependency arrows exactly as they are (front-end PRD roadmap -> 控制台 Dashboard 快照同步, -> Agent Runner 会话持久化, and Agent Runner 会话持久化 -> 归档证据浏览).
4. Keep "控制台 Dashboard 快照同步 / 未开始" and "归档证据浏览 / 未开始" nodes unchanged.

B. Right-hand PRD detail pane (it must describe the running PRD instead of the archived one)
5. Header title: replace "前端 PRD 路线图" with "Agent Runner 会话持久化" in the same bold dark-navy typeface and the same size and baseline. Keep the amber pill "等待 CI/CD" immediately to the right of the title (move it right as needed so title and pill stay on one line, with a small gap), and keep the outlined button "查看 Issue" at the far right unchanged.
6. Path line under the title: replace the current path with exactly "tasks/pending/P1-FEAT-20260916-161500-agent-runner-session.md", same small gray-blue type, one line.
7. Meta line below it: "P1 · Issue #91 · 验收 2/8 · 更新于 2026-09-16".
8. Keep the pale-blue card "此 PRD 的 CI/CD 自动修复" unchanged, including "当前生效：关闭" and the three-state control labels "跟随全局", "强制开启", "强制关闭".
9. Tabs row: keep "PRD 原文" and "CI/CD 1" unchanged; change the middle tab to "验收证据 2". The "CI/CD 1" tab stays the active blue tab with its underline.
10. Inside the "CI/CD 检查未通过" problem card, replace the failing check name "frontend-public / typecheck" with "backend / typecheck"; replace the error sentence with "mypy 类型检查失败：SessionSnapshot 缺少 resume_token 字段"; replace the code line inside the pale pink code box with "'SessionSnapshot' has no attribute 'resume_token'". Keep the red "失败" pill, the red exclamation icon, the "查看 GitHub 检查" link, the round summary line and the action link unchanged.
11. Keep the sentence "自动修复已关闭；问题保留在详情中，不会启动修复 Agent。" unchanged.

Exact strings that must be legible and correctly spelled:
"前端 PRD 路线图", "已归档", "Agent Runner 会话持久化", "运行中", "控制台 Dashboard 快照同步", "归档证据浏览", "未开始", "等待 CI/CD", "查看 Issue", "tasks/pending/P1-FEAT-20260916-161500-agent-runner-session.md", "P1 · Issue #91 · 验收 2/8 · 更新于 2026-09-16", "此 PRD 的 CI/CD 自动修复", "跟随全局", "强制开启", "强制关闭", "当前生效：关闭", "PRD 原文", "验收证据 2", "CI/CD 1", "CI/CD 检查未通过", "backend / typecheck", "mypy 类型检查失败：SessionSnapshot 缺少 resume_token 字段", "'SessionSnapshot' has no attribute 'resume_token'", "失败".

Constraints: this is a minimal targeted edit, not a redesign. Do not add or remove nodes, tabs, buttons or cards. Do not change the sidebar, repository list, toolbar or any other text. Keep the exact same 1536x1024 canvas, flat light UI style, and no watermark.
```

### 2. DashScope `bailian_image_edit`（未采纳）

- 工具：MCP `bailian-image` 的 `bailian_image_edit`；日期：2026-09-16；入参：本图 data URI；结果：输出 1248 × 832
- 结果：蓝框已移到运行中节点、标题换对，但整图重绘——删除了「前端 PRD 路线图」节点、路径行出现错字 `agent-runder`、旧 check 行未替换而是并列新增、标签页计数被改成 `验收证据 6 / CI/CD 2`

```text
这是一张后台控制台 Roadmap 页面的高保真 UI 截图。请只做以下两处修改，其余像素、文字、布局、配色完全保持不变。
一、左侧依赖图：去掉『前端 PRD 路线图』节点上的蓝色选中边框和淡蓝底色，改成与其它节点一致的白色卡片和浅灰细边框；把同样的蓝色粗边框和淡蓝底色加到中间『Agent Runner 会话持久化』节点上，保留它原有的『运行中』蓝色小标签。节点文字、连线、其它节点不变。
二、右侧 PRD 详情面板：把标题『前端 PRD 路线图』改为『Agent Runner 会话持久化』，字体字号字重保持不变；标题右侧的琥珀色『等待 CI/CD』标签保留并顺移到新标题右边；标题下方路径行改为 tasks/pending/P1-FEAT-20260916-161500-agent-runner-session.md；再下面一行改为 P1 · Issue #91 · 验收 2/8 · 更新于 2026-09-16；标签页『验收证据 6』改为『验收证据 2』；问题卡里失败项名称由 frontend-public / typecheck 改为 backend / typecheck，红色说明文字改为『mypy 类型检查失败：SessionSnapshot 缺少 resume_token 字段』，粉色代码框内文字改为 'SessionSnapshot' has no attribute 'resume_token'。
除此之外不要改动侧栏、仓库列表、顶部工具条和自动修复开关卡片里的任何文字与控件；不要新增或删除元素；保持 1536x1024 画布。
```

## 基底图出处

本图分两层来源，不要混为一谈：

- **编辑基底** `roadmap-prd-controls-evidence-autopilot.png`：OpenAI 内置 ImageGen，模式 `precise-object-edit`，2026-09-16。该次调用的完整提示词保存在 `../roadmap-prd-controls-evidence-autopilot.md` 的「最终编辑提示词」一节。
- **本图修订前的初版**：同样由 OpenAI 内置 ImageGen 以上一张图为输入编辑得到，要求「顶部仓库控制条保留全局自动修复 CI/CD；右侧详情增加 `跟随全局 / 强制开启 / 强制关闭` 三态控制、当前生效值、`CI/CD 1` 标签、多轮摘要和问题卡」。该次调用只留下了摘要，**未保存提示词原文**；此处不反向编造。修订后的完整出处以本旁车为准。

## 派生关系

本图是 `roadmap-prd-cicd-01-waiting.png`、`roadmap-prd-cicd-02-repairing.png`、`roadmap-prd-cicd-03-rechecking.png`、`roadmap-prd-cicd-04-passed.png` 四个状态图的编辑基底，对应同名旁车文件。
