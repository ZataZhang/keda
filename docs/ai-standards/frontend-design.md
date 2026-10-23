# 前端设计规范

适用于 `frontend-public/`（Next.js App Router + Tailwind）与 `frontend-admin/` 的界面代码。
`tests/playwright-e2e/` 是独立 TypeScript 包，不强制套用本页。

本页只写**已经在本仓库里生效的约定**，以及给新界面定口味时需要一致的那几件事。它不是
设计系统文档，不重复 Tailwind 自身语义；新增视觉约定先改这里，再改代码。

## 总原则

1. **颜色永远伴随文字**：状态徽章、异常提示、外链胶囊都要有可读文案，颜色只做加速识别。
   读屏与色觉障碍用户必须仅凭文字拿到同样信息（见 `components/dashboard/runtime-task-list.tsx`）。
2. **只扩展现有语义**：不引入新的状态色系，也不为「更好看」给同一语义换色。
3. **密集界面优先一致性**：看板、表格、列表页宁可牺牲留白，也不为单行元素发明新的圆角/字号。

## 颜色语义

中性骨架统一用 `slate`（浅色 `white/slate-50`，深色 `slate-950/slate-900`）。语义色对照表：

| 语义 | Tailwind 家族 | 典型用途 |
|---|---|---|
| 就绪 / 成功 | `emerald` | `agent/ready`、通过校验 |
| 运行中 | `amber` | `agent/running`、进行中的队列 |
| 评审 / 等待 | `blue`（评审）、`sky`（监督中） | PR 审查、待人工处理 |
| 失败 / 错误 | `red` | `agent/failed`、CI 红灯 |
| 阻塞 / 未知 | `slate`（400/500 边框与文字） | 依赖阻塞、状态未知 |
| Issue 外链 | `sky` | 看板行右侧 `Issue #N` |
| PR / 合并请求外链 | `violet` | 看板行右侧 `PR #N`（沿用 GitHub 合并请求配色） |

徽章配色只在 `components/ui/badge.tsx` 的 `badgeVariantClasses` 里维护，页面不要手写状态色。

## 圆角阶梯

| 层级 | token | 用途 |
|---|---|---|
| 关闭按钮 / 贴边图标按钮 | `rounded-xs` | Dialog、Sheet 右上角关闭 |
| 菜单项 / 列表项内小块 | `rounded-sm` | Dropdown 选项、内联小块 |
| 按钮 / 输入框 / 徽章 / 下拉面板 / 外链胶囊 | `rounded-md` | 默认层级，绝大多数元素 |
| 对话框 | `rounded-lg` | Dialog 容器 |
| 卡片 | `rounded-xl` | `components/ui/card.tsx` |
| 头像 / 状态圆点 / 纯装饰圆形 | `rounded-full` | 仅限真正的圆 |

**胶囊类元素（可跳转 / 可复制的外链标签）用 `rounded-md`，不要用 `rounded-full`**：
它和同一行的按钮、徽章并排，全圆角会让行高与视觉基线都显得突兀。

## 字号与行高

| 字号 | 用途 |
|---|---|
| `text-xl`（20px） | 页面标题 |
| `text-sm`（14px） | 正文、表格、按钮 |
| `text-xs`（12px） | 徽章、默认元信息 |
| `text-[11px]` | 高密度元信息：看板行右侧外链、表格小字、时间列 |

`text-[11px]` 的元素必须显式写行高（`leading-4`），否则继承的 `line-height` 会随父级字号漂移，
同一行的两个胶囊就会出现高度差。数值、时间、命令一律 `font-mono`。

## 间距节奏

- 页面容器：`p-4 lg:p-6`，区块之间 `gap-4`。
- 卡片：`py-6` + 内容 `px-6`（来自 `Card` 组件，不要在外面重复内边距）。
- 列表行：密集行 `px-3 py-2`；行内元素 `gap-2`；按钮组 `gap-1`。
- 表格：单元格 `py-2 pr-3`，表头 `text-xs text-slate-500`。

## 深色模式

每个浅色类都必须配一个 `dark:` 对偶，不允许只写一半：

| 浅色 | 深色 |
|---|---|
| `bg-white` | `dark:bg-slate-950` |
| `bg-<color>-50` | `dark:bg-<color>-950/50`（带透明度，保持层次） |
| `text-<color>-700` | `dark:text-<color>-300` |
| `border-<color>-200` | `dark:border-<color>-900` |
| `hover:bg-<color>-100` | `dark:hover:bg-<color>-900/60` |
| `text-slate-900` | `dark:text-slate-50` |
| `border-slate-200` | `dark:border-slate-800` |

## 交互约定

- **外链**：`target="_blank"` + `rel="noreferrer"`，点击走 `lib/open-external.ts` 的
  `openExternalUrl`（桌面端要转给系统浏览器）；带修饰键（Cmd/Ctrl/Shift/Alt）的点击保留浏览器默认行为。
- **可复制**：复制按钮只写剪贴板，**不触发跳转**；成功后 1.5 秒内图标切成 `Check`，不弹 toast。
  实现参考 `components/agent-runner/copyable-command.tsx` 与 `ExternalLinkPill`。
- **行内操作**：不要把 `<a>` / 次级 `<button>` 嵌进行级 `role=button`，否则会被降成整行一个按钮；
  链接和操作要作为行容器的兄弟节点。
- **图标**：统一 `lucide-react`；与文字同行时 `size={11}`–`14` 并加 `aria-hidden`；
  纯图标按钮必须同时有 `aria-label` 和 `title`。

## 不要做

- 不要用 AI 生成的界面截图当验证证据（截图必须是真实入口跑出来的，见 `docs/ai-standards/testing.md`）。
- 不要为单个页面新造状态色或圆角档位。
- 不要用颜色单独表达状态、异常或成败。
- 不要把外链胶囊做成全圆角，也不要给 `text-[11px]` 元素留空行高。

## 自检

改完界面后至少做一次真实入口观察，并量关键元素的计算样式（高度、颜色、边框）：

```bash
# 起前端，用 Playwright 读 computed style / bounding box 对比
just run frontend-public
```

看板这类同排元素，重点确认两侧胶囊高度一致（例如 `getBoundingClientRect().height` 相等）。
