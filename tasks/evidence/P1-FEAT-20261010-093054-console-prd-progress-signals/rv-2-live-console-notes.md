# rv-2 跨进程真实入口取证说明（原「live console 缺口」已闭合）

捕获代码版本：`HEAD=44a33d6b`，分支 `issue-274`。

## 与上一轮的差异

上一轮本文件名为 `rv-2-live-console-gap.md`，披露的是「TestClient 覆盖了判定逻辑，但跨进程/
真实 TCP 那一维没跑」。经复核该阻塞并不成立（`kc console` 自动挑空闲端口、认证为空实现），
本轮已用真 console + curl 补齐，故改名为 notes 并删除原 gap 文件。

## 复现命令

```bash
bash tasks/evidence/P1-FEAT-20261010-093054-console-prd-progress-signals/scripts/rv2_live_console.sh
```

与 rv-1 共用同一份隔离 fixture（`rv1_fixture.py` 建的 `progress-fixture` / `nobase-fixture`
两个真实 git 仓库），只是打的是 curl 而不是浏览器。逐条断言与请求原文写入
`rv-2-live-console.txt`，末尾汇总「断言通过 N 条」，任一失败即 rc≠0。

本项最初记为「29 条全通过」其实是半截绿灯：runner 用 `bash -lc` 复跑时命中系统 `/bin/bash` 3.2，
`$description（` 的全角首字节被并入变量名触发 `set -u` 中断，脚本在第 1 节就停了而退出码仍为 0。
修法是变量一律 `${}` 包裹 + `lib_console.sh` 的 EXIT 哨兵（未 `mark_completed` 一律判失败），
manifest 里再钉住「✅ …全部通过」这条收尾标记与 stderr 的哨兵反例。现在的 29 条是修好后
重新跑出来的，`rv-2-live-console.txt` 即该次全文（29 个 PASS、0 个 FAIL）。

## 实测结果（29 条断言全部通过）

1. **隔离检查**：`GET /api/v1/agent-runner/repositories` 只返回两个 fixture 仓库，真实仓库
   `keda-main` 不在其中——证明这轮取证没有碰真实仓库。
2. **分支优先**：运行中 PRD 的 `/progress` 返回 `prd_source=worktree`、
   `worktree.branch=progress-running`、`checklist.checked=3`（主仓库同名副本是 1）、
   `impact.touched=7`、`impact.unresolvable=2`。
3. **manifest 归属与允许集合**：同名异构的 `shared.txt` 取工作区版本并标 `source=worktree`；
   主仓库独有文件标 `source=main`；符号链接、11MiB 超限文件、隐藏文件、子目录内文件四类
   一律不入列——与 artifact 端点的允许集合保持一致。
4. **artifact 内容归属**：`GET .../evidence/<shared.txt token>` 响应体是
   `WORKTREE 版本内容`，不是主仓库的 `MAIN 版本内容`；`main-only.txt` 仍可读且取到主仓库内容。
5. **安全负控**：符号链接逃逸（指向 `/etc/passwd`）、隐藏文件、超限文件、路径遍历名
   `../config.toml`、畸形 token 五种请求全部 4xx；响应体既不含 `root:` 也不含仓外
   `config.toml` 的 `agent_runner` 片段——拒绝且零泄露。未知 `repo_id` 返回 400。

## 为什么这段仍然需要 pytest 那一段

curl 段构造的是「正常 + 越界」两侧的真实 HTTP 行为；worktree 查询失败/多重匹配 → `unavailable`
而非伪装空态等分支，由 `tests/test_backlog_prd_{progress,worktree,evidence}.py` 的集成断言覆盖
（评审修复轮后为 55 条）。两段在同一条 command 里以 `&&` 串联，任一失败即整项红。

> **订正（Pre-PR Review 修复轮）**：本文件此前把「损坏清单回退文件名前缀」「mixed 归属」也写成
> 由 pytest 段覆盖，但首轮树内并没有这两条用例（只有「清单缺失回退」）。评审修复轮已补
> `test_attribution_filename_fallback_when_manifest_damaged`、
> `test_unreadable_manifest_falls_back_to_prefix`、
> `test_attribution_mixed_when_manifest_and_prefix_both_used`，以及两条目录枚举失败降级用例
> （`test_unreadable_evidence_dir_marks_unavailable_not_empty` /
> `test_endpoints_survive_unreadable_evidence_dir`），现该声明成立；pytest 计数同步为 55。

## 验证层级

`real console`（真实进程、真实 TCP、真实 git worktree、真实磁盘），无 mock、无 `|| true` 兜底。
