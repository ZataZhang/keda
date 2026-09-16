# Repairing State Prompt

- 工具：OpenAI 内置 ImageGen
- 模式：`precise-object-edit`
- 编辑目标：`roadmap-prd-cicd-auto-repair.png`
- 要求：保持页面布局不变；第 1 轮 typecheck 失败后自动启动修复 Agent，展示复用当前 PR/分支、运行耗时和“修复中”状态。
