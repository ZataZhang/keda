# 验证计划

## 范围

本计划覆盖 PRD §7.6 的 rv-1 至 rv-6。六项均有负向对照、真实命令和单独证据文件。取证脚本位于本目录的 `scripts/`，不会进入代码变更。

| RV | 真实入口与检查 | 负向对照及预期失败 | 证据 |
|---|---|---|---|
| rv-1 | 临时 Git 仓库配置 `platform = "gitea"`，执行 `uv run kc --repo <repo> issue list`；要求退出码 1、输出包含 `gitea` / `platform` / `not implemented`、说明仅支持 `github` 和回退方法，且没有 `Traceback`。 | 同一真实入口改用 `platform = "github"`，错误地要求出现 gitea 未实现提示；断言应失败。 | `rv-1-gitea-not-implemented.txt` |
| rv-2 | 临时仓库配置 `platform = "gitlab"`，执行真实本地命令 `uv run kc --repo <repo> agent lifecycle list --scope repository`；要求加载配置时退出码 1，并列出字段、值和允许取值。 | 变更前真实 CLI 曾接受 `gitlab` 并以 0 退出；错误提示断言应失败。证据保留基线输出及当前重新运行的绿结果。 | `rv-2-unknown-platform.txt` |
| rv-3 | 真实 CLI 分别对未声明平台与显式 `github` 执行 `issue list`；要求退出码相同，且没有平台未实现或配置非法提示。 | 将其中一个对照改为 `gitea`，仍要求无平台提示；该断言应失败。 | `rv-3-default-unchanged.txt` |
| rv-4 | gitea 配置下执行本地 `agent lifecycle list --scope repository`；要求退出码 0 且输出 `scope=repository`。 | 在同一仓库执行 `issue list`，验证平台访问失败不会被本地配置命令路径触发。 | `rv-4-local-command-ok.txt` |
| rv-5 | 运行两个平台相关测试文件（`--testmon-noselect`），再以项目支持的 `JUST_LOCAL_TEST_TARGET=tests/test_agent_runner_config.py` 目标运行 `just test`；脚本拒绝零测试结果，并检查架构搜索、签名比较和接口 diff。完整测试档的实际运行结果单独保留。 | 断言 `validate_repository_platform("gitlab") == "gitlab"`；应抛 `ValueError` 并列出允许值。 | `rv-5-pytest.txt`、`rv-5-full-suite.txt` |
| rv-6 | 严格构建 MkDocs，并搜索配置指南和 Agent Runner 指南中的 `platform` 说明与交叉引用。 | 在 Git 基线版本的配置指南中搜索同段 `platform` / `gitea` 说明；搜索应无匹配。 | `rv-6-docs-build.txt` |

每条 RV 的可复现脚本及命令均在证据目录的 `scripts/` 下。运行 `kc evidence check` 会按 manifest 重跑六项并校验输出断言。
