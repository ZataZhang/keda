# 验证计划 · Roadmap 单 PRD 控制、归档证据与 Autopilot 自动推进

PRD：`tasks/pending/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot.md`（交付时归档至 `tasks/archive/`）
分支：`feat/roadmap-prd-controls-evidence-autopilot`（worktree `/Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot`）
基线代码树：`8c8403e`（`git merge-base HEAD main`；开工时为 `4d4dd12`，交付在 PR #147 合并后 rebase 到该提交以便复用其 TOML 写回原语）
执行时间：2026-09-20
执行器：CodeBuddy Code（会话内自证）；随后由独立 verifier Agent 复核

## 验收项 → 可执行验证映射

| 验收项 | 验证方式 | 命令 / 入口 | 证据文件 |
|---|---|---|---|
| rv-1 默认依赖图选中可启动 PRD → 唯一 canonical start API；阻塞项不可绕过；三视图一致 | e2e：真实 Next.js 静态页 + 真实 API client，Roadmap API 用 Playwright route 提供确定性仓库数据 | `just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts` | `rv-1-roadmap-single-start.webm` |
| rv-1 生产静态入口 | 既有 console 静态导出 spec（`/app/roadmap/` 直接刷新不 404） | `just e2e tests/workflows/console-served-static.no-auth.spec.ts` | 见 evidence report 复跑摘录 |
| rv-2 开关只改 `autopilot.enabled`，保留注释/同级键/未知子表；写后 fresh load 读回 | system：真实 `iar console` + 真实临时 Git 仓 + 真实 `.iar.toml`，GET→PATCH→GET + 未知仓库负控 | `bash tasks/evidence/<stem>/scripts/rv-2-autopilot-config-diff.sh` | `rv-2-config-diff.txt` |
| rv-2 页面行为（PATCH 走 query + body，降级文案，auto_merge 不被联动） | e2e：真实页面 + route stub | `just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts` | `rv-2-autopilot-control.webm` |
| rv-3 归档证据 manifest 与 artifact 受限读取；攻击面全拦；空态给出解析位置 | system：真实 console + 真实文件系统（含隐藏文件 / 子目录 / 逃逸符号链接 fixture） | `.venv/bin/python tasks/evidence/<stem>/scripts/rv-3-path-attack-matrix.py` | `rv-3-path-attack-matrix.txt` |
| rv-3 真实页面呈现 | e2e + 真实 console 截图 | `bash tasks/evidence/<stem>/scripts/rv-3-archived-evidence-screenshot.sh` | `rv-3-archived-evidence.png` |
| rv-3 负控（实现前端点不存在） | 在基线 worktree（`git worktree add --detach /tmp/iar-rv3-baseline 8c8403e`）上重放同一请求 | 同上脚本 §5 自动执行 | `rv-3-path-attack-matrix.txt` §5 |
| rv-4 Autopilot 开启后 daemon 下一轮自动晋升下游；关闭零晋升；`auto_merge=false` 零合并 | integration：真实 `advance_roadmap_queue` + 真实仓库 `.iar.toml` loader + fake GitHub/store | `uv run pytest -o addopts="" tests/test_roadmap_advance.py tests/test_roadmap_autopilot_settings.py -k 'autopilot or upstream or disabled' -v` | 见 evidence report §2.3 |
| rv-5 API / core / writer 契约（成功 / 空态 / 非法仓库 / 非法路径 / 写失败） | integration：FastAPI TestClient + 真实临时文件 | `uv run pytest -o addopts="" tests/test_roadmap_api.py tests/test_roadmap_prd_evidence.py tests/test_repository_settings_editor.py -q`（26 passed）与 `… tests/test_roadmap_autopilot_settings.py … -q`（34 passed） | 见 evidence report §2.4 |
| rv-6 全仓质量门禁 | lint / 全量测试 / console 构建 / e2e / 文档构建 | `CI=true just test all`（2368 passed）+ `just lint --reuse` + `pre-commit --all-files` + `just console-sync` + `just e2e …`（18 passed，含 #147 的 lifecycle spec 6 条）+ `uv run mkdocs build --strict` | 见 evidence report §3 |
| 架构红线 | API 不 import tomlkit / 不直接写文件；core 只依赖窄端口；`.iar.toml` 写回走 PR #147 的共享原语、本 PR 不新增第二份；新增端点不复用 `_ROADMAP_CACHE` | `just lint --reuse`（含 check-architecture）+ 定向 `rg` | 见 evidence report §3 |
| 文档同步 | `docs/guides/agent-runner.md` + `docs/api/references.md`；`mkdocs.yml` 无需改动 | `uv run mkdocs build --strict` | 见 evidence report §3 |

## Mock 边界

- **rv-1 / rv-2 页面行为 / rv-3 页面行为（e2e）**：浏览器、Next.js 静态导出、`lib/api` 客户端与全部 Roadmap 组件真实；只有 `/roadmap/**` 的后端 JSON 响应被 Playwright route 替换为确定性数据，用于在 CI 里稳定复现「未开始 / 阻塞 / 已归档」三种形态。start 请求不由测试伪造成功结果，而是捕获 URL 与请求体作为判据。
- **rv-2 配置写回**：真实 `iar console`（真实 FastAPI）+ 真实临时 Git 仓 + 真实 `.iar.toml` 文件；GitHub 不参与。console 的 `console.db` 与 `~/.iar` 通过覆盖 `HOME` 落到临时目录，不触碰开发机真实状态。
- **rv-3 证据读取**：真实 FastAPI + 真实文件系统；fixture 目录刻意包含三类不允许展示的对象（隐藏文件、`scripts/` 子目录、指向仓外的符号链接），以及一个真实可解码的 PNG。
- **rv-4**：GitHub 与 process/store 端口用 fake 记录副作用；配置 loader、依赖评估器、状态解析器与 `advance_roadmap_queue` 真实。
- **rv-5**：FastAPI TestClient 真实；core 端口按测试目标用 fake；writer 测试使用真实临时文件。
- **刻意不 mock**：`resolve_evidence_dir` / `list_evidence_files` 的路径规则、`load_agent_runner_local_settings` 的读回、`_autopilot_enabled` 双门禁、tomlkit round-trip 与 `os.replace`。

## 负控设计

| Oracle | 负控 | 期望差异 |
|---|---|---|
| rv-1 | 在实现前的默认依赖图上点击节点 | 整块画布被 PRD 原文替换且没有「开始此 PRD」，用例失败（`expected_fail` 已写入 PRD） |
| rv-2 | 在基线 worktree（`git worktree add --detach /tmp/iar-rv3-baseline 8c8403e`）上重放同一 GET / PATCH；以及 PATCH 未知仓库 | 基线两条均 404（入口此前不存在）；未知仓库 400 |
| rv-2 | 写后 fresh load 与请求值不一致时 | 用例 `test_set_autopilot_enabled_rejects_stale_fresh_loader` 报错，不返回 200 冒充成功 |
| rv-3 | 在基线 worktree（`8c8403e`）上重放 evidence / autopilot 请求 | 两个端点均 404 |
| rv-3 | 恶意 artifact token（穿越 / 隐藏 / 子目录伪装 / 符号链接逃逸 / 非法 base64） | 全部 400，响应体不含仓外内容 |
| rv-4 | `enabled=false` 的 daemon pass；以及 `auto_merge=false` 的 merge queue | 前者零晋升副作用；后者 `(0, [])` 且零 `merge_pull_request` |

## 对抗自检（实施期执行）

- rv-1 断言的是「唯一 start 请求的 URL 与请求体」+「启动后重新拉取列表后详情状态来自服务端」，而不是本地 toast 或乐观值。
- rv-2 的 diff 断言是**逐行 diff**，并额外统计同级键 / 未知子表 / 注释的保留条数；任何一处丢失都会让统计对不上。
- rv-3 的 manifest 断言的是「真实 `stat().st_size`」与「磁盘允许集合」，并做 fresh-state probe（新增 / 删除文件后列表随之变化），排除服务端与前端缓存。
- rv-3 的截图自检同时检查「真实文件名出现」与「逃逸 / 隐藏文件未出现」，避免截图只证明页面能打开。
- rv-6 在最终实现树上重跑全量门禁；`just e2e` 首轮暴露的受控开关 `check()` 竞态已修复（改为 `click()` + `toBeChecked()`）并复跑通过。
