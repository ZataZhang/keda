# 证据报告 · Roadmap 单 PRD 控制、归档证据与 Autopilot 自动推进

PRD：`tasks/pending/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot.md`
分支：`feat/roadmap-prd-controls-evidence-autopilot`（worktree `/Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot`）
基线代码树：`4d4dd12`（创建 worktree 时的 `main`）
执行时间：2026-09-20
执行器：CodeBuddy Code（会话内自证），待独立 verifier 复核

> 本轮**没有**产出 rv-1 / rv-2 / rv-3 的人读呈递物（录屏、页面截图）。原因与补救见 §4：
> 本 worktree 的 `uv sync` 在本机持续失败（见 §5 环境阻塞），因此 `just console-sync` /
> `just e2e` / `just test` / `mkdocs build` 无法在链路上执行。凡我在本轮声称通过的项目，
> 都是用主仓 venv + `PYTHONPATH=<worktree>/src` 或 `tsc --noEmit` 真实跑出来的，命令与
> 退出码均在 §1/§2 原样给出；未执行的项目一律标注 `未执行（spec 就绪）`，不冒充已通过。

## 0. 一句话结论

后端链条（受限 `.iar.toml` 写回、证据只读访问、Autopilot 状态聚合、API 契约、现有持续调度
与双重合并门禁）**全部真实跑通**：新增 32 个测试 + 61 个 roadmap 回归测试全绿，架构守卫与
文件行数守卫通过，前端 typecheck 通过。人读呈递物（真实页面录屏 / 截图）与全仓 `just lint
--full` / `just test` / `just console-sync` / `mkdocs build` 因环境阻塞**未执行**，需要在
`uv sync` 可用的环境里补齐后方可走 verifier / 归档门禁。

## 1. 自动化 oracle 结果（真实执行）

| Oracle | 入口（真实执行） | 结果 | 退出码 | 证据 |
|---|---|---|---|---|
| rv-2（写回 + 读回） | `PYTHONPATH=$PWD/src ~/code/keda/.venv/bin/python -m pytest -o addopts="" tests/test_repository_settings_editor.py tests/test_roadmap_autopilot_settings.py -q` | PASS 19 passed | 0 | 本节下方逐项摘录 |
| rv-3（证据受限读取） | 同上 + `tests/test_roadmap_prd_evidence.py` | PASS 13 passed | 0 | §2.2 |
| rv-4（持续调度 + 双门禁） | `tests/test_roadmap_autopilot_settings.py -k 'autopilot or upstream or disabled'` | PASS | 0 | §2.3 |
| rv-5（API / core / writer 契约） | 上述三个新文件 + `tests/test_roadmap_api.py` 等 7 个既有文件；另跑**全量后端测试** | PASS 32 + 61；全量 2304 passed | 0 | §2.4 / §2.5 |
| rv-6（部分） | `frontend-public` `tsc --noEmit`；`hooks/shared/check_architecture.py`；`hooks/shared/check_max_file_lines.py <新增文件>` | PASS | 0 | §2.5 |
| rv-1（真实页面 E2E） | `just e2e tests/smoke/roadmap-controls-evidence-autopilot.spec.ts` | **未执行（spec 就绪，已 typecheck）** | — | §4 |
| rv-6（`just lint --full` / `just test` / `just console-sync` / `mkdocs build`） | — | **未执行（环境阻塞）** | — | §5 |

测试命令的共同前置（本机 `uv sync` 不可用时的替代路径，导入解析到 worktree 的 `src/`，已验证
`backend.__file__` 指向 worktree）：

```bash
cd /Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot
PYTHONPATH=$PWD/src ~/code/keda/.venv/bin/python -m pytest -o addopts="" \
  tests/test_repository_settings_editor.py tests/test_roadmap_autopilot_settings.py \
  tests/test_roadmap_prd_evidence.py -q          # 32 passed
PYTHONPATH=$PWD/src ~/code/keda/.venv/bin/python -m pytest -o addopts="" \
  tests/test_roadmap_api.py tests/test_roadmap_prd_content.py tests/test_roadmap_actions.py \
  tests/test_roadmap_advance.py tests/test_roadmap_state_resolver.py \
  tests/test_roadmap_dependencies.py tests/test_roadmap_prd_scanner.py -q   # 61 passed
```

## 2. 关键观察值（原样摘录，不重建）

### 2.1 rv-2 · Autopilot 写回只改一个键

- 保真断言覆盖了 PRD §7 要求的两类样本：本仓真实的同级键（`merge_method` /
  `require_verifier_pass` / `auto_sign_off` / `merge_check_timeout_seconds`）+ 注释，以及一个
  人工构造的未知子表 `[agent_runner.some_unknown_table]` 与未知键 `unknown_key`。
- `test_set_enabled_changes_only_the_target_key`：写回前后逐行 diff **只有一行**：
  `("enabled = false", "enabled = true")`；行数相等、注释与未知子表仍在。
- `test_set_enabled_toggles_back_without_drift`：来回切换一次后文件与原始文本**完全相等**
  （幂等 round-trip，无格式漂移）。
- API 层 `test_patch_autopilot_persists_and_reads_back`：PATCH 返回 `enabled: true`，随后
  fresh `GET` 仍为 `true`，`persisted_enabled: true`，而 `auto_merge_enabled` 保持 `false`
  ——开关没有联动打开第二道门禁。
- 负控：`test_set_autopilot_enabled_rejects_stale_fresh_loader`（fresh load 与请求值不一致时
  报错，不返回 200 冒充成功）；`test_patch_autopilot_conflicts_when_config_missing`（目标仓
  无 `.iar.toml` → 409，且**没有**凭空创建文件）；`test_set_enabled_leaves_original_intact_on_invalid_toml`
  （解析失败 → 原文件逐字节不变）。

### 2.2 rv-3 · 归档证据来自真实磁盘，攻击面全拦

- `test_manifest_lists_real_files_with_roles`：4 个文件按既有命名约定分别判为
  `evidence_report` / `verifier_report` / `verification_plan` / `artifact`；`size_bytes` 断言与
  真实 `stat().st_size` 相等（不是验收勾选数）。
- `test_manifest_reflects_disk_changes`（fresh-state probe）：新增 `new-file.md` 后 manifest
  从 4 → 5 且包含该文件；删除后回到 4 ——证明既不是前端硬编码也没走服务端 30 秒缓存。
- `test_manifest_empty_state_reports_lookup_path`：无目录时 `exists=false`、空文件列表，并给出
  实际解析位置 `tasks/evidence/<prd-stem>`。
- 攻击面：`../secret.md`、`..\secret.md`、`.hidden`、`.`、`..` 全部拒绝；`test_artifact_rejects_symlink_escape`
  用指向仓外的符号链接验证「解析后父目录必须等于证据目录」这条 containment；API 层
  `test_api_evidence_rejects_malicious_token` 对 `../.iar.toml`、
  `../../../../etc/hosts`、非法 base64 三个 token 全部返回 `400` 且响应体不含证据正文。
- legacy 兼容：`test_legacy_flat_evidence_dir_keeps_flat_semantics` 证明
  `validation.evidence_dir = ".iar/evidence"` 的仓库仍是扁平语义，行为不变。

### 2.3 rv-4 · 现有持续调度链与双重合并门禁

- `test_upstream_merged_promotes_downstream_when_autopilot_enabled`：临时仓 `.iar.toml` 经
  **产品自身 loader**（`load_agent_runner_local_settings`）读出 `enabled=true`；真实
  `advance_roadmap_queue` + fake GitHub/store 下，上游合并后 `reconciled_completed == [upstream]`、
  `started == [downstream]`、下游被打上 `agent/ready`。下游 PRD 显式声明了
  `Depends on tasks/issues: #1`，确保这不是被 discovery 顺手捞起来的。
- `test_autopilot_disabled_promotes_nothing`：`enabled=false` 时 `_autopilot_enabled(config)`
  为 False（既有 daemon 门控据此整段跳过 roadmap 调度；具体 daemon 侧负控见
  `tests/test_roadmap_advance.py::test_gate_disabled_skips_scheduling`）。
- `test_merge_queue_requires_both_switches`：`autopilot.enabled=true` 且
  `safety.auto_merge=false` 时调用真实 `process_merge_queue` → `(0, [])`，fake GitHub 上
  **零** `merge_pull_request` 调用。
- 未为 UI 需求改写 `roadmap_actions.py` / `run_agent_daemon.py` / `agent_runner_merge_queue.py`
  的既有算法：这三个文件在本次 diff 中无改动。

### 2.4 rv-5 · API / core / writer 契约

- 新增测试文件 3 个，共 32 条：`tests/test_repository_settings_editor.py`（8）、
  `tests/test_roadmap_autopilot_settings.py`（11）、`tests/test_roadmap_prd_evidence.py`（13）。
- 回归：7 个既有 roadmap/PRD-content 测试文件共 61 条全绿（含既有的 start / settings /
  advance / gate 用例），未修改这些测试的断言。
- 架构守卫：`hooks/shared/check_architecture.py` → 「共扫描 251 个文件，架构依赖方向全部合法」。
- PRD §7 漂移守卫逐条自查（命令与结果）：
  - `rg -n "tomlkit|os\.replace" src/backend/api src/backend/core/use_cases/roadmap_autopilot_settings.py src/backend/core/use_cases/roadmap_prd_evidence.py` → **无命中**；
  - `rg -n "tomlkit|os\.replace" src/backend/infrastructure/config` → 仅
    `repository_settings_editor.py`（仓库本地 `.iar.toml`）与 `registry_editor.py`（全局
    `config.toml` registry，既有），按目标文件各一份，**没有第二份重复原语**；
  - `rg -n "_ROADMAP_CACHE" src/backend/api/routes` → 命中仅 `list / start / start-global`，
    autopilot 与 evidence 端点不复用该缓存（新增端点处有注释说明）；
  - `rg -n "handleStart|startRoadmapPrd|canStartRoadmapPrd" frontend-public` → 启动入口唯一
    （页面 `handleStart` → `lib/api/roadmap.ts::startRoadmapPrd` → canonical endpoint；
    规则函数 `canStartRoadmapPrd` 由卡片与详情共用）。

### 2.5 rv-6 · 静态质量与全量测试（部分执行）

- **全量后端测试（真实跑完）**：

  ```bash
  cd /Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot
  export PATH="/opt/homebrew/bin:$PWD/.venv/bin:$PATH"   # uv / iar 必须可见
  PYTHONPATH=$PWD/src .venv/bin/pytest -o addopts="" tests/ -q --ignore=tests/playwright-e2e
  # 2304 passed in ~2m56s
  ```

  中途曾出现 4 条失败（`test_agent_runner_console_api.py` ×3、`test_console_processes.py` ×1），
  原因都是子进程找不到 `iar` 可执行文件（`FileNotFoundError: 'iar'`）——把 `.venv/bin` 放进
  PATH 后这 4 条单独重跑 **29 passed**，因此与本次改动无关，记为环境噪声。
  worktree 的 `.venv` 由 `uv sync --all-extras --no-install-project` 装齐依赖（项目本身因
  §5 的 uv 构建失败未安装，故用 `PYTHONPATH=$PWD/src` 提供 `backend` 导入）。

- `frontend-public`：`./node_modules/.bin/tsc --noEmit` → 退出码 0；E2E 目录
  `tests/playwright-e2e` 的 `tsc --noEmit` 对新增 spec 无报错（只有既有的
  `idea-inbox.spec.ts` 一条与本 PRD 无关的历史告警）。
- `hooks/shared/check_max_file_lines.py` 对全部新增/改动文件 → 退出码 0。
- `src/backend/api/routes/agent_runner_roadmap.py` 现有 464 非空行（未到 500）：按 PRD §7 的
  拆分指引应以 500 为界，且拆分会打破既有测试对 `agent_runner_roadmap` 模块的 monkeypatch
  表面（既有用例因此会失联），本轮**保持单文件**，在此登记为待复核项。

## 3. 变更清单（最终实现树）

后端：

- 新增 `src/backend/infrastructure/config/repository_settings_editor.py`：全仓唯一的仓库级
  `.iar.toml` round-trip / 原子替换原语（tomlkit + 同目录临时文件 + 完整模型校验 +
  `os.replace`），只写 `[agent_runner.autopilot].enabled`。
- 新增 `src/backend/core/use_cases/roadmap_autopilot_settings.py`：状态聚合 + 受限写回 + 写后
  fresh load 校验；`daemon_is_running` 复用既有 process supervisor 记录。
- 新增 `src/backend/core/use_cases/roadmap_prd_evidence.py`：manifest 与 artifact 读取，目录
  解析一律走既有 `resolve_evidence_dir`，含 basename / containment / 符号链接 / 大小校验。
- 端口 `IRepositoryAutopilotSettingsEditor` 定义在
  `src/backend/core/shared/interfaces/runner_console.py`；装配新增
  `create_repository_autopilot_settings_editor`（engines factories + core facade 转发）。
- `src/backend/api/routes/agent_runner_roadmap.py`：新增 `GET/PATCH /roadmap/autopilot`、
  `GET /roadmap/prds/{path}/evidence`、`GET /roadmap/prds/{path}/evidence/{token}`，均不走
  30 秒缓存；DTO 与 4xx 映射在路由层。

前端（`frontend-public`）：新增 `components/roadmap/prd-detail.tsx`（统一详情 + 可扩展标签
容器）、`prd-evidence-view.tsx`、`roadmap-autopilot-control.tsx`；`prd-card.tsx` 把可启动判定
提为共享纯函数 `canStartRoadmapPrd`；`prd-content-view.tsx` 的 `onBack` 改为可选（详情标签
场景由统一头部导航）；页面改为 master-detail（三视图共用同一选择/详情/启动规则）；
`lib/api/roadmap.ts` + `lib/api/types.ts` 新增 DTO 与 wrapper。

文档：`docs/guides/agent-runner.md`（Roadmap 统一详情、证据长期事实源、仓库级 Autopilot 开关
与双重门禁、daemon 条件）、`docs/api/references.md`（4 个新端点 + 既有 start 端点的三视图
说明）。未新增长期页面，`mkdocs.yml` 无需改动。

## 4. 未完成项（诚实登记，不是通过项）

- **rv-1 人读呈递物**：`tasks/evidence/.../rv-1-roadmap-single-start.webm` 未采集。E2E spec
  已写好（`tests/playwright-e2e/tests/smoke/roadmap-controls-evidence-autopilot.spec.ts`，覆盖
  默认依赖图选中 → 规范 start 请求捕获 → 阻塞项禁用 → 三视图一致 → 证据标签 → Autopilot
  PATCH 与降级文案），并通过类型检查，但未在真实 console 上运行。
- **rv-2 / rv-3 人读呈递物**：`rv-2-autopilot-control.webm` + `rv-2-config-diff.txt`、
  `rv-3-archived-evidence.png` + `rv-3-path-attack-matrix.txt` 未采集；其中 diff 与攻击矩阵的
  **等价断言已由 §2.1 / §2.2 的自动化测试覆盖**（一行 diff、三类攻击 400）。
- **既有 E2E 的同步改造**：`tests/playwright-e2e/tests/smoke/roadmap-prd-content.spec.ts` 里
  「返回列表」的断言已改为 master-detail 的关闭详情（新增 `prd-detail-close`），同样未实跑。
- **全仓门禁**：`just lint --reuse`、`just lint --full`、`just test`、`just console-sync`、
  `uv run mkdocs build --strict` 本轮均未执行（环境阻塞，见 §5）。

## 5. 环境阻塞（影响后续复核，需要人工处理）

`just worktree` 在本机创建 worktree 时，`uv sync --all-extras` 反复失败：

```text
error: EEXIST: file already exists, mkdir
  '/Users/zata/.cache/uv/builds-v0/.tmpXXXX/.tmp-YYYY'
hint: This usually indicates a problem with the package or the build environment.
```

已尝试：独立 `UV_CACHE_DIR`、`UV_CONCURRENCY=1`、`--no-build-isolation`（先 `uv pip install
setuptools wheel`）、沙箱外执行——全部同样报错，因此 worktree 里没有 `.venv` / 专用数据库，
`just test` / `just e2e` / `just console-sync` 都无法按原样跑。本轮的验证改为主仓 venv +
`PYTHONPATH=<worktree>/src`（已确认导入解析到 worktree 的 `backend`）。前端依赖用主仓
`node_modules` 符号链接替代 `pnpm install`（本机没有 pnpm 可执行文件，只有 corepack 缓存）。

建议在 `uv sync` 可用的环境里补跑 §4 列出的命令与 E2E，并补齐人读呈递物后再走
verifier 复核与归档门禁。
