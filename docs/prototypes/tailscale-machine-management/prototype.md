# Keda 多机连接与远程文档交互原型

这是基于 `frontend-public/` Console 布局与色彩的 **interactive prototype**，使用固定示例数据，不连接 Keda API、Tailscale、SSH、文件系统或 Agent Runner。

## 入口与演示

- 从 [Prototype Hub](../hub.html) 搜索「Keda 多机连接与远程文档」，打开本原型。
- 直接入口：[打开交互原型](./index.html)。静态 HTML，无需构建；从仓库根目录用 `python3 -m http.server 8000` 启动后打开 `http://127.0.0.1:8000/docs/prototypes/tailscale-machine-management/`。
- 可点击流程：`机器管理 → 添加电脑 → Tailscale 加入说明 → SSH/远程登录检查 → 选择授权文件夹 → 完成`；返回机器卡片可浏览远程文档、打开 KedaCode 管理的私有网页预览或打开 Runner 管理页。
- 图片、Markdown 和 PDF 文件行可切换预览；Runner 停止/重启会先出现目标机器与影响范围确认。
- 机器卡片的“浏览文档”和“管理 Agent”是直达入口；点击其余行内区域不承担隐藏动作。原型 Dock 的 Hub、说明、可点击区域与重置属于评审控件，不属于产品界面。

## 状态模型

```text
machines
  ├─ add-machine → wizard-step-1 (same tailnet login / human-only device consent)
  ├─ next → wizard-step-2 (SSH enablement / connection validation)
  ├─ next → wizard-step-3 (human chooses read-only roots)
  └─ finish → machines
machines → files (folder select → file preview) → machines
machines → runner (confirm stop/restart → result toast) → machines
```

## 产品约束与待实现确认

- 人必须在新电脑本机安装并登录 Tailscale、接受系统网络权限、按需批准设备，并选择允许读取的目录。没有独立远程通道时，Keda 无法替人完成新电脑首次入网。
- Tailscale 只负责设备间网络连接。文件访问和 Keda Runner 管理需另有受认证的远程通道；原型表达标准 SSH/SFTP 方案，但授权、密钥保管和跨平台行为仍需由实现与安全评审落实。
- 远程文件只读；Runner 启停是有影响的操作，需要逐次显示目标和范围。原型不代表产品已具备这些权限或接口。

## 来源与维护

- 页面结构参考 `frontend-public/components/layout/app-shell.tsx`、`frontend-public/components/layout/app-sidebar.tsx`、`frontend-public/app/(app)/app/processes/page.tsx` 和 `frontend-public/app/(app)/app/settings/page.tsx`。
- 视觉样式参考 `frontend-public/app/globals.css` 的紫色主色、明亮背景和圆角卡片；新增 UI 不是生产实现。
- 未使用生成图片；静态模拟的文档和图片预览由 HTML/CSS 构成。
