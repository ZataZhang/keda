# Issue 234 最终代码/文档审查

时间：2026-10-07T18:45:35.427318+00:00。工作树 HEAD `23f5abf67d3e0ff4b7e3e07e3e3961e9223cbe05`，尚有交付修改。仅只读审查九份 skill 文件及 `remote_template_skills.py` 安装比较窄差异；未运行测试、未改仓库。不是 PRD evidence verifier，不声称 verifier PASS。route probe 仍待主代理完成。

## Findings

无未解决的具体代码/文档 finding。最终补充修复已仅作用于 create-issue.md：PRD-backed 创建回写本地链接并默认 stage/commit/push；--from-prompt 仅创建 GitHub Issue；恢复检查本地及已发布 PRD 链接，不重置历史。已对照 `labels_issue.py` 分支、`create_issue_from_prompt.py:214-315`、`create_issue_from_prd.py:1174-1193` 与 `publish_prd_file:543-569`，描述符合实际。

## 原 findings 复核

- missing/unreadable PRD 文件后果已改为创建前失败；existing link 默认拒绝/目录跳过与 force 风险描述符合实际。
- hub 新通用规则明确已有授权跨 route/recovery 延续，只在必要信息/授权缺失或范围增加等情形再问，适用于所有 reference confirm 表述。
- CI recovery 记录原始 stored global/per-PRD override并恢复，inherit仅对原本无 override，已解决固定 off/inherit 问题。
- Machine Contract 支持集改为 v3/v4/v5，符合当前 core 权威。
- 加入以实际安装 SKILL.md 父目录定位绝对 reference 的 fallback，解决跨 Agent 专属变量依赖风险；路由原字符串保持。
- hub 57非空行；八路由 description/文件均保留；只读禁令、退出码全表、unknown flag自省、目标必填均常驻。原Safety不变量与重要baseline知识未发现丢失。

## 安装比较代码

受管枚举为 SKILL.md + references/**/*.md，递归且来源于实际发行包；目标额外文件不参与比较；缺文件/字节差异返回false；默认preserve-conflict、force才rmtree/copytree、dry-run不写的原分支保持。旧名清理仍走原全量枚举，未被新受管集合替换。新增私有helper各有多个调用/测试复用，未引入CLI变化。未发现具体产品代码阻塞。

## 验证状态

主代理报告 full test 3445 passed / 1 skipped / 173.44s、真实 init matrix + help/schema、reuse/mkdocs 通过；本审查没有重新执行/独立读取全部原始日志，作为外部已报告验证记录，不能替代 evidence verifier。本人此前运行 specific skill guard 24 passed / 0.19s；最后文案修复后主代理将重跑实际安装产物和目标守卫；本报告不替代该检查。

## 本次阅读快照文件摘要

- `SKILL.md`: `7f927e49d203b89d9129205da164dfea27a62d6867decce6d1a1c1e434f15eac`
- `references/ci.md`: `99d0bf1ba4284a30fdd33815de15ee0e573b410d97bb7af37171db4ab27e34ae`
- `references/create-issue.md`: `50e29643f984118e7b34a4c1d76a3450495a0904acaa9127d1cefa1b8159d80c`
- `references/daemon.md`: `b5d0bd3aa450e3227c4b1cabbf1a25416293b28994998c0792fa887e3ed1cadc`
- `references/issue-inspect.md`: `6477e13466f97e0d5970b2f4c2a9ca80ebb1c4254707df2b5040ef57bd23a3f4`
- `references/run-once.md`: `9bc88e18c3f86155c9aef1ad68b6026d3cbc4f9f8d5125b3ebcd16166e13cec2`
- `references/setup-and-config.md`: `f97d873df65be7d4c48b0c5b470f3d6fd0eab8002a295233543a8e7e63f3faf0`
- `references/triage.md`: `b9ff75c02a667b69ef43dfc9525498f4bcfa6690c1177cd7c9337acb6dd934f0`
- `references/watch.md`: `e45cce7696bfe145df59cb9207f5f9319745af067f9865d93e1d74275b6252a5`

## 评审总结

🔴 严重0；🟠 高0；🟡 中0。
代码状态：通过窄范围审查；文档状态：已修复并复核；证据签核：不在本审查范围。

```json
{"verdict":"approved","summary":"All concrete source/document findings resolved and reconciled against the real CLI implementation; this is narrow code/document approval, not PRD evidence verifier PASS.","findings":[]}
```

复核完成时间：2026-10-07T18:46:16.938778+00:00
