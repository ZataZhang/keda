# rv-1 黄金命令行快照来源（provenance）

- before（旧构造点直采）: `golden-argv-before.json`，由一次性脚本 `export_golden_argv.py` 在**改造前旧树**导出，树 = 基线 commit `c6faff87441dc4d626be4a56f78080a76c62e1de`（分支点，当时 `iar agent doctor` 尚不存在），cwd = 本 worktree 根。
- after（统一构造器直采）: `golden-argv-after.json`，由 `iar agent doctor claude codex kimi --all-profiles --json` 在**最终树**（基线 `c6faff8` + 本次全部未提交改动 staged 后）重采，cwd = 本 worktree 根。
- 比对: `diff golden-argv-before.json golden-argv-after.json` 输出为空（12 条命令行逐字节一致）。
- final-tree 重采时点: 全部代码改动（含 `output_protocols/__init__.py` F401 修复与 `content_generators.py` timeout 透传修复）之后、文档改动之前；文档改动不影响 argv。
- 探测哨兵: `--prompt golden-prompt`（doctor 默认值，与脚本 `GOLDEN_PROMPT` 一致，不含空格）。
