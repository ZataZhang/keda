
# 夜间清理（CI / 重复代码 / 文档 / 依赖）— 2026-10-26

- GitHub Issue: https://github.com/ZataZhang/keda/issues/325

> 每晚 02:00 触发 keda 仓的整理 loop。本次 PRD body 用 triage 决策树给出
> 4 类 scope（CI 失败 / 重复代码合并 / 文档同步 / 依赖升级）的优先级与判定
> 指引。Agent 当晚选 1 个最高 ROI 的做；若 4 类都无事可做，关 Issue + comment
> 收尾，不出 PR。

- Loop id: `nightly-cleanup-keda`
- Target repository: `keda`
- Trigger date: `2026-10-26`
- Last CI status: `success`
- Outdated dependency count: `69`

## 1. CI 失败

- **触发条件**：`last_ci_status` 解析为 `failure` / `timed_out` / `cancelled` / `action_required` 任意非 `success` / `none`。
- **行动指引**：
  1. `gh run view <id> --log-failed` 抓失败日志；
  2. 在 worktree 内复现失败（`uv run pytest <path> -v`）；
  3. 修代码 / 测试 / 配置后 `just test` 全绿；
  4. 出 draft PR，标题前缀 `cleanup(ci):`。
- **无事可做判定**：`last_ci_status` 为 `success` / `none`，或修复尝试一次后回归测试不通过且与失败无关。

## 2. 重复代码合并

- **触发条件**：在 `src/backend/` 内对 helper 类函数做相似度扫描（`pylint --disable=all --enable=duplicate-code` 或 `jscpd` 之类），发现 ≥ 2 处重复块 / 近似函数。
- **行动指引**：
  1. 选 ROI 最高的一处重复（被调用次数多 + 维护者最近修改频繁的优先）；
  2. 抽取公共 helper / 合并到单一函数，调用方全量替换；
  3. `just test` 确认无回归；
  4. 出 draft PR，标题前缀 `cleanup(refactor):`。
- **无事可做判定**：扫描结果为空，或所有重复块的 ROI 都很低（< 5 行 / 1–2 处调用方）。

## 3. 文档同步

目标是补漏：功能修改时仍须同步文档，夜间只沿近期 diff 检查受影响说明，不做全库重写。
构建通过只能证明构建层面的有效性，不能证明行为描述符合代码。

### 检查范围与进度

1. 在本轮 Issue 评论记录检查开始时目标 base 分支的完整提交 SHA，作为固定上界 `head_sha`。
   读取该 loop 最近已关闭 Issue 的文档检查记录，找最近有效的 `checked_through_sha`；
   必须确认它是 `head_sha` 的祖先。不要把 Issue 的关闭或 PR 合并本身当作文档检查完成。
2. 有有效检查点时，从它之后按 base 分支 first-parent 顺序检查，到固定上界为止；
   使用包含合并结果的提交 diff（相对 first parent），避免漏掉合并提交引入的变更。
   首次运行没有检查点时，以最近 7 天作为初始范围，并披露更早历史未覆盖。
   记录读取失败或检查点不再是祖先时，标记范围未确认，不悄悄当作首次运行重置进度。
3. 每轮最多检查最早的 20 个待查提交。逐提交查看 diff，再沿变更符号、入口、配置键与
   直接调用方定位相关文档；单提交过大可分功能检查，未查完前不得越过该提交推进检查点。
   不因代码和文档同一次提交就推定已同步，也不因没有同名文档就判定无需更新。
4. `checked_through_sha` 只推进到连续完整核对的最后一个提交；记录剩余范围，下轮接续。
   有发现但尚未修复时可记录已检查进度，但必须携带未解决项及对应 Issue/PR，后续轮次先
   核对是否已修复或由开放任务承接，避免既丢问题又重复开修复 PR。

### 核对与修复

- **触发条件**：发现有源码依据的行为遗漏或矛盾，例如入口、参数及默认值、配置来源、
  状态转换、持久化、请求范围、缓存与刷新节奏、权限和失败处理描述不符；或者发现相关
  文档的失效路径、符号、链接及应纳入导航的正式页面缺项。
- **行动指引**：
  1. 从生产入口追踪当前实现。按 feature-docs 的方法维护已有指南或功能地图；技能不可用时
     仍按本节执行，不将历史 PRD、旧文档或测试替身当成实现事实。
  2. 每个发现写明“提交/变更符号 → 文档位置 → 当前描述或遗漏 → 实现依据 → 影响”，
     优先修正会误导使用、运维或代码修改的问题，不为措辞偏好制造改动。
  3. 修正相关权威文档和引用；仅新增、移动正式页面或导航确有缺项时更新 `mkdocs.yml`。
     不修改生产代码来迎合旧文档，也不扩成无关文档整理。
  4. 跑 `git diff --check` 和 `uv run mkdocs build --strict`，并回看修改后的行为描述与
     源码是否一致；纯文档改动不声称完成产品行为验证。出 draft PR，标题前缀 `cleanup(docs):`。
- **无事可做判定**：已完成所声明范围的行为核对，没有成立且未被其他任务承接的文档差异，
  并完成适用的链接、导航与构建检查。仅构建通过、检查被跳过或依赖不可用均不代表无差异。

### 每轮记录

无论发现与否，均在本轮 Issue 留下文档检查记录：`head_sha`、起点及首次覆盖限制、
`checked_through_sha`（未检查则不推进）、已查提交与相关文档、发现及源码证据、未解决项及
承接链接、剩余范围、实际验证结果，以及检查耗时。没有适用文档也需简述判断依据。
区分“已核对无差异”“发现待修复”“部分检查”“未执行”，不编造覆盖率。

## 4. 依赖升级

- **触发条件**：`outdated_count` > 5（含 5），或 0.0 ≤ X < 1.0 的小版本（patch）累积 ≥ 10。
- **行动指引**：
  1. `uv lock --upgrade` 一把锁，或手工选高 ROI 的几个直接依赖（被本仓 `import` 的多、安全公告紧的）；
  2. `just test` 确认兼容性；
  3. 若失败，缩小到安全范围（patch only）重试；
  4. 出 draft PR，标题前缀 `cleanup(deps):`。
- **无事可做判定**：`outdated_count` 阈值未触发；或上次升级在 7 天内（避免每晚刷 PR）。

## Triage 优先级

- **当晚只做 1 个 scope**，优先级：`CI > refactor > docs > deps`。
- **判定流程**：
  1. 先按上节触发条件逐个判定（CI 看 `last_ci_status` → refactor 看扫描 → docs 看近期 diff 的行为核对及构建 → deps 看 `outdated_count`）。
  2. 命中最高优先级的 scope 即为当晚目标；跳过更低优先级。文档被跳过时仍记录“未执行”及原因，
     不推进文档检查点；下轮继续累计范围，不按当天时间窗口丢弃积压。
  3. 4 类都未命中 → `gh issue close <N> --comment "no actionable cleanup tonight"`，**不**出 PR。
- **scope 标注**：选定 scope 后，在 worktree 内执行：
  ```bash
  gh issue edit <N> --add-label scope/<x>
  ```
  其中 `x ∈ {ci, refactor, docs, deps}`。`scope/<x>` 4 个 label 需先通过
  `kc labels sync` 在仓库创建（详见 `docs/guides/loop.md`）。

## Delivery Notes

- Recommended branch: `task/<issue-number>-prd-nightly-cleanup-keda-2026-10-26`
- Worktree command: `iar worktree create --branch issue-<issue-number> --base-branch main`
- PR should include: `Closes #<issue-number>`
- Draft PR 标题前缀：`cleanup(<scope>): <一句话描述>`
