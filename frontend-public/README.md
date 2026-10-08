# 前台官网

本目录是 `zata-codes-template` 的前台官网与 Agent 工作区（Public Website + App Shell），基于 Next.js 16 + React 19 + Tailwind CSS v4 + shadcn/ui 构建。

## 技术栈

- **框架**：Next.js 16（App Router）
- **UI 库**：React 19
- **样式**：Tailwind CSS v4
- **组件库**：shadcn/ui
- **HTTP 客户端**：axios
- **表单**：React Hook Form + Zod
- **工作流画布**：@xyflow/react

## 本地开发

```bash
# 安装依赖
pnpm install

# 启动开发服务器（默认端口 3000）
pnpm dev
```

开发服务器启动后访问 `http://localhost:3000`。

后端服务默认在 `http://localhost:8000`，前端通过 Next.js rewrites 代理 `/api/*` 到后端。

## 构建生产产物

```bash
pnpm build
```

产物使用 Next.js standalone 输出，由根目录 `frontend-public/Dockerfile` 构建为生产镜像。

## 项目结构

```text
frontend-public/
├── app/
│   ├── (marketing)/    # 营销页面：首页、Agent 广场、功能、定价、FAQ
│   ├── (auth)/          # 登录、注册
│   ├── (app)/           # 登录后应用页面
│   │   ├── page.tsx     # 根入口：客户端跳转到 /app/backlog（console 落点）
│   │   └── app/
│   │       ├── backlog/      # console 首屏：受管理仓库栏 + 当前仓库 PRD 队列
│   │       ├── dashboard/    # 多仓混排的队列监控总览
│   │       ├── processes/    # 托管进程启停与日志
│   │       ├── repositories/ # 仓库 registry 管理
│   │       ├── stats/        # 完成度统计
│   │       ├── ideas/        # 想法采集与 PRD 草稿
│   │       └── settings/     # 用户设置
│   ├── layout.tsx       # 根布局
│   └── globals.css      # 全局样式
├── components/
│   ├── ui/              # shadcn/ui 组件
│   ├── layout/          # 公共布局（SiteHeader、SiteFooter、AppShell、AppSidebar）
│   ├── agent/           # Agent 相关组件
│   ├── chat/            # 聊天相关组件
│   └── workflow/        # 工作流画布组件
├── lib/
│   ├── api/             # axios 封装与 API 调用
│   ├── console-repository-selection.ts  # console 首屏默认仓库解析（见下节）
│   ├── types/           # TypeScript 类型定义
│   └── utils.ts         # 工具函数
├── public/              # 静态资源
├── next.config.ts
└── Dockerfile
```

## console 首屏落点

`iar console` 打开根路径 `/` 后跳到 `/app/backlog`。Backlog 页首屏自动选中
「启动 console 的那个目录对应的仓库」：后端 `GET /api/v1/agent-runner/console/context`
把进程 cwd 归一到 git 仓库根并匹配 registry，前端再按
**cwd 匹配 → localStorage 记忆 → registry 首个 enabled** 的顺序取舍。
Backlog / Processes / Ideas 三个页面共用 `lib/console-repository-selection.ts`
的 `useRepositorySelection`。完整语义见
[Agent Runner 指南](../docs/guides/agent-runner.md) 的「首屏默认仓库（当前项目）」。

## 升级提示

管理终端顶部的「有新版本」提示条（`components/layout/update-banner.tsx`）比对两侧
版本：**当前版本**取自后端运行时 `GET /api/v1/agent-runner/console/version`
（构建期注入会与实际安装的包漂移，故必须走后端）；**最新版本**由浏览器直查
`https://pypi.org/pypi/kedacode/json`（PyPI JSON API 允许跨域，后端不引入对外
网络调用）。打开 / 刷新时静默查一次，离线、超时、被墙或拿不到任一侧都不显示。

提示条只展示命令（`uv tool upgrade kedacode`，见 `CopyableCommand`），**不替用户
执行升级**——console 不执行 CLI 动作是本前端的既定约定。用户「跳过此版本」按版本号
记入 `localStorage`（`iar.console.skippedUpdateVersion`），该版本不再提示、下个
版本再提示；升级后需重启 `kc console` 才生效。

## 与后端的集成

- 开发时直接请求 `http://localhost:8000`。
- 生产时通过环境变量 `API_BASE_URL` 指向后端服务（如 `http://zata-codes-template-backend:8000`）。
- 认证使用后端签发的 HTTP-only `session_id` cookie，因此与 `frontend-admin/` 管理平台共享登录态。

## Agent 平台功能

- **营销页**：首页 Hero、Agent 广场（`/marketplace`）、功能、定价、FAQ
- **Agent 管理**：创建、编辑、查看 Agent，配置模型、系统提示词、工具
- **会话聊天**：与 Agent 多轮对话，查看工具调用过程与结果
- **工作流编排**：基于 React Flow 的可视化节点画布，保存/运行工作流
- **工具管理**：查看平台预置工具列表

## 容器化

```bash
docker build -t zata-codes-template-public -f Dockerfile .
```

镜像基于 Node.js 运行 Next.js standalone，端口 `3000`。
