---
name: prd
description: RV 证据 fixture 用的最小 prd skill 契约桩（仅满足 daemon 启动预检）。
Machine-Contract-Version: 5
---

# PRD skill（fixture 桩）

本文件只用于隔离 RV fixture：daemon 启动预检要求可解析的 prd skill 且
Machine-Contract 主版本落在支持集合（v3 / v4 / v5），并要求兄弟
`scripts/prd_contract.py` 存在。RV 场景不生成 PRD（就绪 Issue 直接带
`agent/ready` 标签入队，不走 rework-prd 通道），因此这里只需通过版本与
接线预检，不承载真实格式教学。

## Machine Contract

- Change Log 条目使用六段式结构。
- Acceptance Checklist 使用 `- [ ]` / `- [x]` 语法。
- 证据命名 `rv-<item_number>-<slug>.<ext>`。
