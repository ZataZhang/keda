# kc 缺少独立的随包 skill 安装/刷新入口

- GitHub Issue: https://github.com/ZataZhang/keda/issues/245

## 背景

随发行包发行的 operator skill 已随产品改名（`iar-operator` → `kedacode-operator`，源 `src/backend/engines/agent_runner/templates/skills/kedacode-operator/`）。安装逻辑本身是完整的：`install_packaged_operator_skill`（`src/backend/engines/agent_runner/remote_template_skills.py`）幂等（一致即 skip）、fail-closed（不覆盖外来内容）、并带旧名 digest 清理（用户未改动的旧名副本自动删除，改动过则保留）。

但安装的唯一触发入口是 `kc init`（`src/backend/api/cli_init.py`，`kc takeover` 内部也走 init），而 init 的第一步「生成 `.kedacode.toml`」遇已存在的配置文件会直接抛错退出（"KedaCode local config already exists … Use --force to overwrite it."），skill 安装步骤根本执行不到。

## 复现

1. 机器上已装旧版随包 skill（如 `~/.claude/skills/iar-operator`），且任一常用仓库已有 `.kedacode.toml`；
2. 升级 kedacode（随包 skill 已改名 `kedacode-operator`）；
3. 在该仓库跑 `kc init` → 报 "local config already exists … Use --force" 后退出，skill 未装；
4. `kc init --force` 会把深度定制过的 `.kedacode.toml`（lifecycle_agents、prompt 模板、verification_commands 等）整文件重新生成——不可接受；
5. `kc schema --json` 全命令树无任何 skill 子命令。

结果：升级后旧名 `iar-operator` 残留在各用户级 skills 根（内容仍是 IAR 时代的描述），新名 `kedacode-operator` 无法通过任何受支持路径安装；`prd` / `code-reviewer` 远端模板 skill 的刷新也被同一耦合挡住。

## 影响

- CLI 升级后 agent 引用旧 skill 名/旧描述，操作面提示与真实 CLI 脱节；
- 「只想重装/刷新 skill」这一高频场景没有一等命令，当前只能绕过（在无 `.kedacode.toml` 的仓库跑 init，或手写脚本直调安装函数）。

## 建议方案

- 最小改：`kc init` 在「配置已存在且未 `--force`」时不再整体失败——提示跳过配置写入，继续执行 skill 同步（remote template + packaged operator），一行分支即可修复升级场景；
- 更完整：新增 `kc skill install [--force]`（复用 `install_remote_template_skills` / `install_packaged_operator_skill` 同一实现，目标根仍由 `resolve_user_skill_install_roots` 决定），init 组合调用它；可顺带提供 `kc skill list` 展示各根的安装状态。

## 验收口径

- [ ] 已存在 `.kedacode.toml` 的仓库中，`kc skill install`（或修复后的 `kc init`）能安装 `kedacode-operator` 到全部用户级 skills 根；
- [ ] 未被用户改动的旧名 `iar-operator` 副本被自动清理；改动过的保留并给出提示（`--force` 才覆盖）；
- [ ] 全程不修改已存在的 `.kedacode.toml`；
- [ ] 本地 `SKILL.md` 与远程模板不同的 `prd`/`code-reviewer` 在无 `--force` 时保持 fail-closed（报错不覆盖），行为不回退。

## 非目标

- 不改 runner 运行期的 worktree 内 skill 装载行为；
- 不改旧名 digest 清理与 fail-closed 语义本身。
