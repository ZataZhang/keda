# 实施证据报告

## 交付与验证结论

主文件缩为 57 非空行，八条口语路由指向同一 Skill 下的八份 references；退出码、目标约束、只读禁令与 Safety 知识留在 hub。安装器按实际发行包的 SKILL.md + references/**/*.md 比较字节，忽略用户额外文件。没有 CLI 表面、数据库或前端变更。

| Oracle | 实际结果与本地证据 |
|---|---|
| rv-1 | PASS：真实 init 安装九文件逐字节相同；独立干净 dry-run 零写入。见 rv1-installed.txt、rv1-clean-dry-run.txt。 |
| rv-2 | 独立外部最终快照的 clean / invalid flag / restored 检查，完整输出见 rv-2-final-*.txt；非法旗标必须报来源文件。 |
| rv-3 | PASS：真实 init 的 baseline / unmanaged / conflict；修改受管文件保留用户字节，新增笔记仍 up-to-date。见 rv3-*.txt。 |
| rv-4/5 | PASS：目标测试实际执行24项；description覆盖8关键词，route完整，hub57非空且共享底线保留。见 rv-4-5-targeted.txt。 |
| rv-6 | PASS：实际 --help 与 schema --json stdout 与干净改名前置 PR 树53497764逐字一致；stderr独立保留。见 rv6-*.txt。 |
| rv-7 | REVIEW_INCISION / INCONCLUSIVE：隔离 HOME 的真实 Claude headless 因未登录无法执行路由；没有创建前四检查或只读不执行的行为证明。按 PRD 降级到真实安装产物+真实初始化 Skill inventory+description八关键词，结合最终九文件全文与独立静态流程审查。详情见 rv-7-final-availability.txt 与 rv-2-7-probe-report.md。没有把未执行标为 PASS。 |
| rv-8 | rv-8-skill-reading-view.md 是最终九文件全文，final-code-review.md 是独立代码/文档终审；Human-Confirmed 保持未勾。用户允许以代码审查合并，未冒充用户人审。 |
| rv-9 | PASS：真实冲突 dry-run 零写入、实装默认preserve-conflict、force恢复随包内容；见 rv9-*.txt及rv3-conflict-preserved.txt。 |

## 门禁与最终树绑定

CI=1 just test all：3445 passed / 1 skipped / 173.44s；全部默认 full lint hooks通过。最后一次说明文本事实更正不改Python或命令，目标资源测试重新运行24 passed，真实init矩阵亦重新收集。just lint --reuse 通过；uv run mkdocs build --strict成功。原始完整日志见rv-gates-*，不将旧缓存跳过算作执行通过。

最终 Python 与九资源文件逐文件 sha256 见 final-resource-fingerprints.json；本报告与它们同一提交，提交后 verifier 必须以干净 HEAD 复核。后续 PRD归档/报告提交只更新交付记录；若代码/资源字节再变，重新收集相应证据。

## 真实边界、失败与限制

实际CLI/Typer→init安装编排→文件比较→copytree→fresh磁盘字节均真实，远程模板下载没有替换。GH拒绝命令替身只阻止隔离验证仓库的标签同步，未替换安装边界。HOME、仓库、配置各自隔离，不复制密钥。

Builder曾因后台环境缺少终端代理变量多次模板下载超时；保留备份后接管窄范围验证与文档纠错，未重启完整实现。后台进程下次启动会继承现有网络环境。rv-2探针显式 -o addopts= 关闭testmon选择，防止Markdown负控被缓存跳过。

rv-7隔离认证与跨Agent实际行为尚未证明；该限制按PRD允许的降级交付并保留明示。全文阅读视图与机器输出均已保留；未取得用户四项Human-Confirmed，归档保持待人工验收。GitHub行为不属于本PRD安装与文档范围。
