# 独立 verifier 报告 · Roadmap 单 PRD 控制、归档证据与 Autopilot 自动推进

PRD：`tasks/archive/P1-FEAT-20260916-122645-roadmap-prd-controls-evidence-autopilot.md`
分支：`feat/roadmap-prd-controls-evidence-autopilot`（worktree `/Users/zata/code/keda-worktrees/feat/roadmap-prd-controls-evidence-autopilot`）
基线（开工点）：`4d4dd12`
复核时间：2026-09-20
复核者：独立 verifier Agent（与执行器不同的会话上下文，只读）

## 冻结凭证

| 轮次 | 冻结提交 | tree | `git diff <base> <head> -- src tests` sha256 | 工作区 |
|---|---|---|---|---|
| 第一轮（实现与 FR 对照 / 证据真实性） | `6e9374d9c83730a93d0ac6743b2b53df1292fd08` | `77f06bb7f8a129a62ebfc97ba1d6369b93aef44b` | `c7f3909c628c4869d26f912c1a5da830628c04b5d720eab57da51e0a124d3647`（base `4d4dd12`） | 干净 |
| 第二轮（整改确认 + 回归） | `dff673a9a24de102e54bfd7a33daa1949729a86b` | `2b1aa17abb537997cbaff798544c4525b8af5ac9` | `dd160edbf1e186f1c1eef70540372d63c87d00c9218a9be0ea75acc228eca3ac`（base `fc74576`） | 干净 |
| 第三轮（rebase 到 PR #147 后的原语收敛与合并回归） | `aefad2eb55181148a6c2b2ba314c4f2672e057fb` | `ba26fde46a152a9c78aafd64df32cd1448b4dce6` | `45ee33161d1cab1a694e00b73ce4a657c541d51143bcbdd9d5e3e9c96c60f4fb`（base `8c8403e`） | 干净 |

第二轮之后的收尾改动**只动文档**（evidence report / verification-plan / PRD 的计数与措辞），未触碰 `src/` 与 `tests/`，因此第二轮的 `src tests` 冻结哈希在归档时仍然有效。

## 复核方法

- 对照 PRD §10 Functional Requirements（FR-1..FR-12）与 §9 Acceptance Checklist，逐条核到冻结代码的真实行为，而不是注释与 docstring。
- 自行构造攻击输入复现安全边界（路径穿越、绝对路径、隐藏文件、子目录伪装、符号链接逃逸、非法 base64、超限文件、非布尔 `enabled`）。
- 复跑关键命令：`.venv/bin/python -m pytest -o addopts="" …`、`just lint --reuse`、`npx playwright test --list`、`file` / `sips` 校验呈递物有效性。
- 核对证据报告里的每个数字与其对应产物（`rv-2-config-diff.txt`、`rv-3-path-attack-matrix.txt`、录屏、截图）。
- 用基线 worktree（`git worktree add --detach /tmp/iar-rv3-baseline 4d4dd12`）真实执行「实现前」负控。

## 第一轮发现与处置

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| F1 | 中 | `GET /roadmap/autopilot` 在 `.iar.toml` 的 `autopilot.enabled` 为非布尔（`"yes"` / `1`）时返回 **500**：受限端口的 `ValueError` 未转换，路由只 catch `RoadmapAutopilotError`。破坏 FR-12/rv-5 的稳定 4xx 契约 | 已整改：`load_autopilot_state` 统一把 `ValueError` 转成 `RoadmapAutopilotError`；新增 `test_get_autopilot_rejects_non_bool_enabled` 断言 400。第二轮实测 `"yes"` / `1` / `"true"` / `"false"` 均 400，合法 `false` 仍 200 |
| F2 | 低 | `build_evidence_manifest` 会列出超过 10 MiB 的文件，而 artifact 端点对它必然返回 400，页面给出必然失败的下载动作 | 已整改：manifest 跳过超限文件；新增 `test_oversize_file_is_excluded_from_manifest_and_rejected_by_artifact`。第二轮实测 10 MiB+1 被排除且被拒，恰好 10 MiB 两者皆允许（边界一致） |
| F3 | 中 | `rv-2-autopilot-config-diff.sh` 原先**没有**基线负控，但 PRD / verification-plan 声称「实现前 404」 | 已整改：脚本加入真实基线 GET + PATCH 负控，产物重新生成。第二轮独立确认脚本在基线 `4d4dd12` 上 GET / PATCH / evidence 三端点均 404，且产物走的是「已执行」分支而非「未执行」回退 |
| F4 | 低中 | 证据报告 / verification-plan 计数与实际产物不一致：新 E2E spec 实为 5 条（报告写 6）、攻击矩阵 11 例含 1 例合法 200（报告写「全 400」）、rv-5 的 `real_entry` 为 26 passed 而报告只写 32 | 已整改：逐条改为实测口径（见 evidence report §1/§2.4 与 verification-plan） |
| F5 | 低 | `roadmap-graph/timeline/list.tsx` 在 PRD Change Impact Tree 中被标为 `[修改]`，实际未改 | 已整改：PRD 改为 `[未改动]` 并写入 §13 Final Reconciliation 与 Change Log |
| F6 | 低 | PRD §9 Validation 条目称「rv-1/rv-3 静态图就地嵌入」，但 rv-1 只有录屏（规范本就允许录屏免嵌） | 已整改：条目改为「rv-3 的 PNG 已嵌入；rv-1/rv-2/rv-3 以录屏为主，按规范录屏免嵌」 |
| F7 | 低 | PRD §9 Human-Confirmed 条目勾 `[x]` 又自述「执行器未代用户声称已逐条过目」，自相矛盾 | 已整改：条目改为「已采集并呈递（**不代表用户已逐条过目**）」，并注明用户以「都做，帮我归档」授权、可回退 |

## 第二轮回归检查

- 改动面：2 个源文件（净 +12 行，纯异常转换与一个 `continue` 跳过）+ 2 个测试文件。
- 指定 4 个测试文件 **38 passed**；roadmap 相关合计 **235 passed**；全量 `CI=true just test all` **2306 passed**。
- **无行为回归**；`just lint --reuse` 5 个 hook 与 `pre-commit --all-files` 17 个 hook 全过。
- 第二轮又发现 2 处**文档计数漂移**（由本轮新增测试引起）：rv-4 的 `-k` 选择由 12 → **13 passed**；PRD §9 里 rv-5 仍写「32 passed」。两处均为文档层，已修正（evidence report §1/§2.3、PRD §9）。产物无变化。

## 未视为问题的项（说明理由）

- **`prd-card.tsx` 的列表/时间轴卡片仍有「开始」按钮**：这是 FR-11 要求保留的既有行为；PRD §1 的「启动按钮放在统一详情头部」针对依赖图节点不膨胀为卡片。两条路径都走同一个 `handleStart` → 唯一 canonical start endpoint，不构成第二条启动路径。
- **`src/backend/infrastructure/config` 下有两个 `tomlkit` writer**：`registry_editor.py` 管全局 `config.toml` 的 repositories 子树（既有，D-05），`repository_settings_editor.py` 管仓库级 `.iar.toml`（本次新增）。目标文件不同，符合 D-05/D-08 的意图；PRD §9 原措辞「写回逻辑单处」已按「每个目标文件单处」修正。
- **与 `P1-FEAT-20260918-110027` 的 TOML 原语收敛未在本 PR 完成**：该 PRD 尚未合并，其分支另建了 `toml_section_editor.py`。属跨 PRD 协调，已在证据报告 §5 与本 PR 描述中登记，不构成本 PRD 的行为缺陷。

## 第三轮发现与处置（rebase 到 PR #147 之后）

第三轮独立复核的 5 项检查（行为等价性 / 失败路径 / 是否只剩一份写回 / 合并是否丢东西 / PRD 与证据是否对齐）全部 PASS，另有 2 项发现：

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| G1 | 中 | `tests/playwright-e2e/tests/workflows/lifecycle-agent-matrix.spec.ts` 的齿轮用例在 `goto` 后立刻 `count()`，SPA 未加载完仓库列表时 `gearCount === 0` 会静默 `test.skip`——上一轮证据里「lifecycle spec 复跑证明齿轮按钮仍可用」因此不成立（跳过 1 条，且正是齿轮路径）。非本轮引入（spec 与 `8c8403e` 逐字节相同，属 #147 遗留） | 已整改：补 `waitForLoadState('networkidle')`（相邻的「Agent 覆盖」用例早就这样修过）；该 spec 由「6 passed, 1 skipped」变为 **7 passed**，齿轮抽屉真跑通过；证据口径同步修正 |
| G2 | 低 | `repository_settings_editor.set_enabled` 的 docstring 仍写「并原子替换文件」，实际已委托共享原语 | 已整改：改为「写回由共享原语原子替换」 |

## 结论

**PASS**（第三轮整改确认 + 合并回归通过；全部遗留项已处置，不涉及产品行为与证据产物）。

- 第一轮：`PASS with findings`（7 项，全部已处置）。
- 第二轮：`PASS with findings` → 遗留 2 项文档计数漂移 → 已修正。
- 第三轮（rebase 到 PR #147 后）：`PASS with findings` → 1 项 E2E 静默跳过 + 1 项 docstring 措辞 → 均已修正。
- 冻结凭证：见上表；第三轮之后仅文档与测试驱动方式的收尾改动，已随同一 PR 提交。
