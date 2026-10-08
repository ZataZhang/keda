# Human Review Checklist — P2-BUG-20261008-145100-kc-skill-reinstall-entry

本页汇总本 PRD 交付后**仅剩的 1 项人工确认**。回复方式：对「结论」一项回答「同意」或写出你希望的退出码取值。

## 结论：同意 `kc skill install` 对两类远程模板失败统一返回退出码 1 吗？

- **PRD 位置**：§2 Human Review Map（唯一决策）＋ §9.2 Human-Confirmed 第 1 项。
- **判错的代价**：若你期望 fail-closed 有独立退出码（如 conflict=4），接受 1 会让依赖退出码区分「远程不可用」与「用户改动被拒」的自动化脚本失去信号——但目前没有任何 `--json` 契约覆盖此命令，影响限于人工排障习惯。
- **背景**：安装实现用同一个异常类型（`RemoteTemplateSkillInstallError`）表达「远程拉取失败」和「本地改动 fail-closed 拒绝覆盖」，命令树无法区分两者；统一映射为 1 后，`kc skill install` 与 `kc init` 对同一失败同码，不新增错误语义。
- **物证**：
  - `tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-conflict.txt` —— 本地改动 `prd` 后无 `--force` 运行的真实输出：`error: Refusing to overwrite user-owned skill 'prd' at …`，退出码非零（脚本断言为 1）。
  - `tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-force.txt` —— `--force` 后退出码 0，用户内容被远程模板替换。
  - 负控测试 `tests/test_cli_skill_install.py::test_skill_install_remote_template_failure_exits_one_like_init` —— 断言两命令同码（1）。
- **复核命令**（可选，约 10 秒）：`open "tasks/evidence/P2-BUG-20261008-145100-kc-skill-reinstall-entry/rv-3-conflict.txt"`，确认错误行含 `Refusing to overwrite user-owned skill 'prd'` 且脚本记录的退出码非零。
- **回复格式**：回答「同意」即完成验收；若不同意，写明你建议的退出码取值与适用失败类别。

---

附：全部机器证据（不需要逐项审）见同目录 `…evidence-report.md` 与 PRD §9.2。
