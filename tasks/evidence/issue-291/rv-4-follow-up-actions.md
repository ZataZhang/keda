# Issue #291 交付门禁阻塞项（Realistic Validation 第 4 项附属证据，脚本生成）

生成命令：`uv run python tasks/evidence/issue-291/scripts/rv-4-gate-failure-attribution.py`（随第 4 项命令一起执行；失败清单、对比修订、生效配置值均取自当次运行）。

## 当次结论

- 分支范围：`src`/`tests` 相对 merge base `e6b60d94` 的 Python 改动数 = 0（本 Issue 只改随包 Skill 文案）。
- 目标分支复现：`main@e6b0df59` → `target_ref_pre_existence=confirmed`。
- 当次运行：HEAD 有 6 个关注用例失败，其中 3 个判为 host_state_config_pins_retired_binary，3 个判为主线既有失败。
- 本机生效配置：`~/.kedacode/config.toml` 的 `[agent_runner.console] runner_command = ['iar']`。
- 最小修复验证（当次运行）：删掉该行后这 3 个用例全部转绿（exit=0，其余配置逐行原样，仅去掉 1 行 `runner_command` 钉值）。

## 需要人工处置（两项都超出本 Issue「只改随包 Skill 文案」的范围）

### 一、本机状态目录残留退役命令

- 现象：托管进程按配置拼出 `iar run --all-ready …`，而 `iar` 已随 `27ad6b57 refactor(cli): remove IAR runtime compatibility` 从 PATH 移除，子进程直接 `FileNotFoundError`。
- 受影响用例：

  - `tests/test_agent_runner_console_api.py::test_process_lifecycle_via_api`
  - `tests/test_agent_runner_console_api.py::test_duplicate_daemon_rejected_via_api`
  - `tests/test_agent_runner_console_api.py::test_audit_endpoint_lists_actions`

- 机制：配置源顺序是 `(env, registry, toml, session_toml, init)`（`src/backend/infrastructure/config/settings.py:341-347`），TOML 高于 init kwargs，所以 fixture 用 `AgentRunnerSettings(console=…)` 注入的 fake 命令必然被全局 config.toml 覆盖。这是测试隔离方式与优先级约定不匹配，不是产品缺陷。
- 为什么 `kc config migrate` 不解决：它的钉子清理表 `_LEGACY_SECTION_PIN_VALUES`（`src/backend/engines/agent_runner/repository_local_migration.py:51-57`）不含 `runner_command`。
- 动作（当次实验已验证）：删掉该行后这 3 个用例全部转绿（exit=0，其余配置逐行原样，仅去掉 1 行 `runner_command` 钉值）。所以只需删除或注释该文件里 `runner_command` 这一行，让默认值按 `kc` 解析，再重跑 `just test all`；执行 agent 被禁止写 worktree 之外的文件，故只能报告，不能代办。

### 二、主线三个过期断言

- 受影响用例：

  - `tests/test_worktree_cli.py::test_create_or_reuse_worktree_links_frontend_node_modules`
  - `tests/test_worktree_frontend.py::TestEnsureFrontendNodeModules::test_stale_main_checkout_lockfiles_are_not_reused`
  - `tests/test_worktree_frontend.py::TestLinkFrontendNodeModules::test_links_node_modules_for_root_and_nested_projects`

- 现实现契约：`_try_reuse_main_checkout_dependencies` 只在两棵树的 `package.json` 相同且至少一份锁文件一致时才复用主检出 `node_modules`（`src/backend/core/use_cases/worktree_frontend.py:611-641`）；上述用例的 fixture 两棵树都只有 `package.json`，所以「拒绝复用 / 不建符号链接」正是设计行为。`tests/test_worktree_frontend.py:536` 期望的 `lockfiles differ` 也已被实现改成 `dependency inputs are missing, different, or unverifiable`。
- 动作（择一，建议另开 Issue，不要塞进本 docs-only 交付）：① 给两处 fixture 的两棵树补同名同内容锁文件，保持用例原意「有锁文件才复用」；② 或把断言改为实现的实际契约文案；③ 若认定「无锁文件也应链接」才是正确契约，则改实现并同步 PRD 与 `docs/`。
- 归属核对：`tasks/pending/` 下没有任何 PRD 提到 `runner_command` 或这几条用例（2026-10-20 全量 grep 无命中）；`P1-REFACTOR-20261020-021715-purge-residual-iar-naming.md` 只管协议标记、目录名与代码标识符，并明确写着不搬动用户磁盘上的旧状态。两类失败都需要新开 Issue——等那份 PRD 落地不会自动清掉本门禁。
- 附带发现：只补锁文件还不够。锁文件齐备后新契约会**先尝试真实安装**，本机有 pnpm 时 `create_or_reuse_worktree` 会直接跑 `pnpm install`，于是这条「断言回退符号链接」的用例变成看主机 PATH 脸色的非确定用例；下面的补丁用工具链桩把它固定成回退路径。

### 已验证的测试侧补丁（在 HEAD 的隔离副本上 3 passed，本 Issue 不落地）

```diff
--- a/tests/test_worktree_cli.py
+++ b/tests/test_worktree_cli.py
@@
 from backend.core.use_cases import agent_runner_worktree_create as worktree_create_module
+from backend.core.use_cases import worktree_frontend
@@
 def test_create_or_reuse_worktree_links_frontend_node_modules(
     tmp_path: Path,
+    monkeypatch: pytest.MonkeyPatch,
 ) -> None:
@@
     (repo_path / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
-    _run_git(repo_path, "add", ".gitignore", "frontend/package.json")
+    (repo_path / "frontend" / "pnpm-lock.yaml").write_text("lockfile\n", encoding="utf-8")
+    _run_git(
+        repo_path,
+        "add",
+        ".gitignore",
+        "frontend/package.json",
+        "frontend/pnpm-lock.yaml",
+    )
@@
+    # 主机装了 pnpm 时新契约会先真实安装，符号链接只是安装不可用时的回退；
+    # 本用例断言回退路径，因此把包管理器探测固定为不可用。
+    monkeypatch.setattr(worktree_frontend.shutil, "which", lambda _name: None)
+
     manager = WorktreeManager(repo_path, SubprocessRunner())
--- a/tests/test_worktree_frontend.py
+++ b/tests/test_worktree_frontend.py
@@
-        assert "lockfiles differ" in project_record.detail
+        assert "must exist and match in both checkouts" in project_record.detail
@@
-        _make_frontend_project(repo_path / "frontend")
-        _make_frontend_project(repo_path / "frontend-admin")
+        _make_frontend_project(repo_path / "frontend", lockfile_names=("pnpm-lock.yaml",))
+        _make_frontend_project(
+            repo_path / "frontend-admin", lockfile_names=("pnpm-lock.yaml",)
+        )
@@
         _write_file(worktree_path / "frontend" / "package.json", "{}\n")
+        _write_file(worktree_path / "frontend" / "pnpm-lock.yaml", "lockfile\n")
         _write_file(worktree_path / "frontend-admin" / "package.json", "{}\n")
+        _write_file(worktree_path / "frontend-admin" / "pnpm-lock.yaml", "lockfile\n")
```

## 对交付门禁的后果

- 充分性已实测（2026-10-20，一次性测量）：在 `080be16d` 的 detached worktree 里应用上面的测试补丁，并把 `~/.kedacode` 的 skills 与「仅删掉 `runner_command` 钉值」的 config.toml 复制到临时家目录后跑全量 pytest，结果 `4642 passed, 1 skipped, 0 failed`；与基线 `6 failed + 4636 passed + 1 skipped` 同为 4643 条收集数，即这 6 项被翻绿且没有用例被跳过或消失。结论：下面两项处置做完，`just test all` 就会转绿。
- 该测量不是 CI 结论：它用本仓库 venv 与 `PYTHONPATH` 遮蔽到 worktree 副本（`pyproject.toml`/`uv.lock` 与基线零漂移），并刻意不写用户真实状态目录。
- `just test all` 是最终树门禁；上述两项未处置前本 Issue 过不了门禁。本轮取证不跳过、不缓存、不弱化该门禁，也不伪造通过标记。
- oracle 的结论随主线变化：用例在 `main` 修好后转绿，本文会改写为「无需处置」；若某条用例在 `main` 绿而在本分支红，oracle 直接 exit 1 拦下回归，不会放行。
