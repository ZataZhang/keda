# Verification Plan — P1-FEAT-20261009-133425-lifecycle-agent-model-settings

- Issue: <https://github.com/ZataZhang/keda/issues/262>
- Canonical PRD: `tasks/archive/P1-FEAT-20261009-133425-lifecycle-agent-model-settings.md`
- 本文件是 PRD §7.6 Realistic Validation Plan 的评审可执行视图：检查点、真实入口、
  判别性负控与复跑命令一一对应，不改写验收口径（口径以 PRD 为准）。
- 证据目录：`tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings/`
  （原始 artifact 按仓库约定不入版本控制，只有本目录的 `*.md` 报告入库）。

## 检查点

| id | 验收口径（PRD §7.6 behavior） | 真实入口 | 层级 | 必需 |
|---|---|---|---|---|
| rv-1 | CLI 能列出九阶段和 fallback 有效配置，并在显式范围内持久化阶段绑定、预设定义与有序回退候选 | `kc agent lifecycle list/set/unset`、`kc agent preset set`、`kc agent fallback list/candidate …`（隔离 fixture 的机器级 + 仓库级文件），写后新 CLI 进程 fresh read | R2 smoke | 是 |
| rv-2 | Settings 统一页面可切换 global/repository 生命周期范围、展示九阶段最终值与来源、编辑预设/绑定；同页执行器回退区可设置候选预设并按明确范围持久化 | 生产路由 `/app/settings/lifecycle/` + 真实 `GET/PATCH /api/v1/agent-runner/lifecycle-settings`、`GET/PUT /api/v1/agent-runner/agent-fallback-candidates` + 真实 Playwright `tests/playwright-e2e/tests/workflows/lifecycle-settings.spec.ts` | R2 e2e | 是 |
| rv-3 | 历史查询、PRD 覆盖、单次运行旗标、无 fallback preset 映射时的既有默认行为及既有 Agent-only API 保持兼容，模板缺失仍 fail-fast；并覆盖预设删除引用完整性 | `kc agent presets`、`kc agent doctor --lifecycle … --json`、`kc run/ask --help`、`tests/test_lifecycle_agents_console_api.py`、真实 `PATCH/GET /lifecycle-settings`（删除仍被引用的预设） | R1 integration | 是 |

## 判别性负控（先证会红，再证变绿）

- **rv-1**：省略写命令 `--scope`、repository scope 省略 `repo_id`、引用未知 preset、把别的
  Agent 的 preset 绑给 claude、对无 effort 参数模板的 Agent 设非空 effort → 各自在触达文件
  前非零退出（`USAGE(2)` / 校验错误），目标 TOML 字节不变（`shasum` 前后比对）。
- **rv-2**：`rv2_negative_control.cjs` 先把三条持久化断言跑红（页面未写入即断言失败），
  再用正控跑绿；窄屏用例以真实红态（`rv-2-e2e-narrow-screen-red.txt`）驱动 AppShell/AppSidebar
  的 <md 布局修复。
- **rv-3**：`rv3_delete_integrity.sh` A/B 段——删除仍被绑定的预设期望 422 且文件字节不变；
  C 段——越带外悬空绑定读取期望聚合端点九键 200 且标注 `preset_unresolved`（去容错即 500）；
  D 段——删除「仓库覆盖全局」的预设放行 200 且引用回落全局预设。

## Mock 边界

可 stub：外部 Agent CLI / provider 模型请求。
不得 mock：Typer 命令树、配置发现与 merge、`toml_section_editor`、HTTP 路由与 schema、
core resolver、前端 API client、页面持久化写入、stdout 与退出码、写后 fresh read。

## 复跑命令

```bash
EV=tasks/evidence/P1-FEAT-20261009-133425-lifecycle-agent-model-settings
bash $EV/scripts/rv1_capture.sh $EV            # rv-1（负控在前 + 正控写盘 fresh read）
bash $EV/scripts/rv2_run.sh $EV                # rv-2（自拉起生产栈：console-sync → 后端 → 浏览器采集 → 真实 e2e）
bash $EV/scripts/rv3_capture.sh $EV && bash $EV/scripts/rv3_delete_integrity.sh $EV   # rv-3（兼容段 + 删除完整性段）
bash $EV/scripts/rv1_final_tree.sh $EV         # 最终树定向复验（打印 head_commit / head_tree / working_state_tree）
bash $EV/scripts/r5_refactor_equivalence.sh $EV  # 第五轮模块搬迁的 AST 等价探针（R5_NEGCTL=1 先判红）
JUST_FULL_TEST_FLAGS=--no-testmon just test all  # 全量测试（本地 `just test` 因 --testmon 可能零收集，不作判据）
uv run mkdocs build --strict
```

## 交付门禁

- `git diff --check`、`SKIP=check-test-flag uv run pre-commit run --all-files`、
  `just test all`、`uv run mkdocs build --strict`。
- 前端另有 CI 门禁不在 `just test all` 内：`pnpm run typecheck`、`pnpm run build`
  （`.github/workflows/ci.yml` frontend-build job），交付侧以本地复跑为补充证据。
- 独立 verifier 裁决与 PRD 归档同为 runner-owned 门禁，第八轮交付时均已完成并按证据回填
  PRD §9 的 `[x]`（verifier 对 sha `b10a7e06` 出具 PASS，产物 `verifier-response.txt`；
  PRD 位于 `tasks/archive/`）。仍留待人工的只有 `Human-Confirmed` 五项，验收状态横幅按
  公式保持 🧍 待人工验收。
- 远端门禁状态与本地证据分开陈述：本地复跑不等于 GitHub CI 已跑。第八轮把分支合并进
  main 的最新头（`29a5ea32`）后，须在合并树上重跑本文件列出的全部命令，推送后再以
  check-runs 的实际结果声明远端状态；在那之前不得写"远端已验证"。
