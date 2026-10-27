# 验证计划（执行侧）— Agent 可用性引导与自定义 Agent 注册

PRD：`tasks/pending/P1-FEAT-20261020-011007-agent-availability-and-custom-registry.md` §7.6 Realistic Validation Plan
被验证代码：基线 `c0e3a084efd2fee4414915d23d129842a2c031f3`；实现改动（`src/` / `tests/` / `docs/` / `frontend-public/` / `config.toml`）已提交为 `90a66e13d14df508bb7e09887f2b2181f771b09e`，本轮证据在该 HEAD 上重跑
采集时间：2026-10-19T20:39Z–21:12Z（UTC），四条 oracle 在**最终脚本版本**上重跑一遍后再定稿记录件；第二轮恢复后按 runner 口径重跑于 22:41:40Z–22:42:12Z。

## 口径

- 四条 oracle 全部走真实入口：`kc agent setup` / `kc agent add` / `kc agent list` / `kc agent doctor` / `kc agent preset set` / `kc agent lifecycle set` / `kc agent fallback candidate add` / `kc run --issue … --dry-run --json` / `kc console` + 真实浏览器。没有用组件预览、手工注入状态或 TestClient 替代任何一项。
- 每条都可由 `evidence.json` 的 `command` 原样复跑（runner 会用 `bash -lc` 在工作树根目录重执行并按字面 substring 断言其 stdout）。断言里没有 `|| true` 兜底，脚本任一检查点失败即 exit 1。
- 关键值（可用 Agent 清单、bin 路径、配置哈希、Issue 快照、worktree 摘要、attempt 账本行数）都由被测路径自己产生并被脚本原样记录，脚本不重建「已安装」常量。
- 脚本内的断言解释器一律钉到项目环境（`PYBIN=$(uv run --no-sync python -c 'import sys; print(sys.executable)')`），不依赖 PATH 上的 `python3`：runner 用 `bash -lc` 复跑时那是系统 3.9，没有 rv-1 配置核对需要的 `tomllib`（3.11+），而项目 `requires-python = ">=3.11"` 保证钉过去的解释器够用。

## 隔离方式（四条共用）

```
env -i HOME=<临时 home> PATH=<只含 stub 与必需工具的临时目录> \
  UV_CACHE_DIR=<复用本机缓存> XDG_CACHE_HOME=<本机缓存> KEDACODE_CONFIG=<临时 config.toml> \
  uv run --no-sync kc …
```

维护者真实的 `~/.kedacode/config.toml` 与 `~/.kedacode/console.db` 不被读写；「未安装」由受限 PATH 造成，是机器事实而不是伪造。

## 各项计划与实测结果

| RV | 真实入口 | 期望（PRD §7.6） | 实测 | 结论 |
|---|---|---|---|---|
| rv-1 | 临时 HOME + 只含 claude stub 的 PATH 下 `kc agent setup --yes --agent claude`，真实 PTY 下裸命令看首启提示 | exit 0；配置只写 `implementation = "claude"`；无 presets / lifecycle_presets；重复运行不产生差异；提示只出现一次 | 负控 exit 3 且字节不变（seed 与负控后同为 `842049520cf3…`）；正控写入后 `2aafa48b67f3…`；第二次 setup `already_satisfied: true`、哈希不变；PTY 三轮提示计数 = 1 | ✅ 绿 |
| rv-2 | `kc run --issue 291 --dry-run --json`（预设指向未安装的 codebuddy） | exit 3；错误点名阶段 / 预设 / agent / bin；Issue 标签评论、worktree、attempt 无新增 | exit 3，stderr 一字点名四项并给出 `kc agent setup` / `--fix`；Issue #291 标签 `agent/ready,status/backlog,type/refactor` 与评论 0 前后逐字段相同；worktree 摘要前后同为 `eb1a70bc…`；账本 0→0 | ✅ 绿 |
| rv-3 | `kc agent add myagent --bin <stub> --arg=--print --write --yes` → 新进程 `kc agent list` / `kc agent doctor` | 注册块只含显式参数；list `available=True` + `bin_path`；doctor argv 与输入一致；重名 / 未注册协议 / 缺 prompt-flag 均 exit 2 且字节不变 | 全部命中：`[agent_runner.agents.myagent]` + `profiles.run`（仅 `--print`）；`available=True bin_path=<stub>`、声明用途 `['run']`；`doctor argv: [<bin>, "--print", "golden-prompt"]`；`doctor --lifecycle implementation` 解析到 myagent；三条负控 exit 2、哈希 `cf6982447a55…` 前后相同 | ✅ 绿 |
| rv-4 | `kc console` + 真实浏览器打开生产路由 `/app/settings/lifecycle/`（沙箱把 implementation 绑到未安装的 codebuddy） | 未安装项有明确标记、可用项没有；页面仍可浏览与编辑 | implementation / fix 两行渲染「未安装（PATH 中找不到 codebuddy）」且无路径标记，6 行 claude 只有 stub 路径、无未安装标记，review 行 codex 同样标未安装；选择器选项「codebuddy · 未安装」vs「claude」；页面 HTTP 200；两张真实页面截图落盘 | ✅ 绿 |

## 负控矩阵（必须能变红）

- rv-1：同一路径下选未安装的 codebuddy（期望 exit 3 + 配置字节不变）；重复 setup（期望哈希不变）。
- rv-2：① 预设改绑可用 claude → 期望从 3 变 10，证明门禁不是无条件阻断；② 缺失主 Agent + 显式配置可用回退候选 → 期望仍 3 且无替代 Agent 执行记录，证明回退不能掩盖缺失。两条方向相反，任何一条被写成常量都会红。
- rv-3：dry-run 不写盘；重名 / 未注册协议 / `--prompt-delivery flag` 缺 `--prompt-flag` 三条均须 exit 2 且哈希不变。
- rv-4：驱动对每行做双向断言（后端 `available=false` ⇔ 有未安装标记且无路径标记），并校验 console 日志出现 `listening on`（端口撞上遗留实例时直接 FAIL，不拿别人的面板冒充本次证据）。

## 已知偏差与限制（详见 verifier-report）

1. rv-2 的期望退出码 10 依赖 `--dry-run --json`；表格模式 dry-run 走的是另一条输出路径、退出码为 0。
2. rv-3 未使用 PRD 原文的 `kc agent doctor myagent --all-profiles --json`：该旗标枚举全部内置用途，对只声明 `run` 的自定义 Agent 按既有契约报错，而该行为在 main 上逐字节相同、并被 §9「内置 Agent doctor 快照一致」锁死为不得改动。改用同一段 argv 组装路径的 `kc agent doctor myagent --json` 与 `kc agent doctor --lifecycle implementation --json`。
3. rv-4 需要 `just console-sync` 产出随包面板静态产物（gitignored）；脚本已内置该前置检查，缺失时先构建再继续。
4. 循环任务的**触发**路径（`src/backend/core/use_cases/loop_fire.py`）仍用内置注册表解析，注册后的自定义 Agent 能被创建 / 列表 / doctor 识别，但暂不能被 fire 执行——已作为 known limitation 记入 PRD Change Log。
