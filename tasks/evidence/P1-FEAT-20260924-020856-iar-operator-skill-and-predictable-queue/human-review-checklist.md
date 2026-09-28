# Human Review Checklist — IAR 操作 Skill 与可预期队列

Linked PRD: `tasks/pending/P1-FEAT-20260924-020856-iar-operator-skill-and-predictable-queue.md`

> ✅ **已确认（2026-09-28）**：下方全部条目由人审确认通过，确认结果已回填 PRD §9.2 Human-Confirmed，PRD 随后归档到 `tasks/archive/`。本清单保留为人工审查留痕。

## Decisions

- [x] Confirm only current Machine Contract v3 is supported; v1 is obsolete and unknown versions fail closed.
- [x] Confirm ready Issues without `priority/P0`…`priority/P3` rank after explicit P3.

## Visible outcomes

- [x] Read the full [`iar-operator` Skill](../../../src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md) in the PR and confirm its operation routing, side effects, and daemon lifecycle guidance.
- [x] Review clean-install and same-name conflict dry-run output in the PR evidence comment; confirm the conflict plan preserves the user-owned Skill.
- [x] Confirm the queue preview behavior and documented 100-Issue candidate window are acceptable.

Merging the linked PR means accepting these decisions and visible outcomes and authorizing post-merge archival, provided required gates remain green and the merged tree matches the verified tree.
