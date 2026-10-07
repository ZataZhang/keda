# 验证计划

最终产品边界：真实随包 Skill、真实 CLI/Typer、安装编排、受管文件比较与 copytree；不修改 CLI 命令树。

| Oracle | 验证 |
|---|---|
| rv-1 | 新隔离 HOME / 仓库 / 空配置中运行实际 kc init 与 dry-run，比较九份文件字节，检查 dry-run 零写入。 |
| rv-2 | 最终文件运行目标测试，关闭 testmon 选择；外部快照注入不存在旗标、要求文件与旗标被点名，恢复后重跑。 |
| rv-3 | 两个独立 HOME 建立 up-to-date 基线；增加非受管笔记仍 up-to-date；修改受管说明默认 preserve-conflict。 |
| rv-4/5 | 实际命令与资源测试核对八路由、关键词、目标契约、退出码、只读禁令和 ≤70 非空行。 |
| rv-6 | 真实 --help/schema 输出与前置改名 PR 的干净树逐字比较。 |
| rv-7 | 真实 headless Agent + 全新 HOME 可用性与路由轨迹探针；不可用时按 PRD 明确记录 INCONCLUSIVE，并保留安装产物及全文阅读视图。 |
| rv-8 | 本目录 rv-8-skill-reading-view.md 展示最终九文件全文；四项 Human-Confirmed 不代替用户勾选。 |
| rv-9 | 实际 init 冲突、冲突 dry-run、force；比较用户字节与零写入，记录完整输出。 |

补充仓库检查：CI=1 just test all、just lint --reuse、uv run mkdocs build --strict；最终检查绑定实际代码/资源树。没有前端或数据库变化，不声称 UI E2E。

唯一外部替身是隔离安装 fixture 的 gh 拒绝命令，以避免 GitHub 标签写入；真实模板 Git 下载保持实际网络。安装入口、比较及文件写入均未替换。原始产物在本目录本地保留，文本报告随提交；不存在密钥复制。
