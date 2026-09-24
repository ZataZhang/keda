# Human Review Checklist — IAR 操作 Skill 与可预期队列

Linked PRD: `tasks/pending/P1-FEAT-20260924-020856-iar-operator-skill-and-predictable-queue.md`

## Decisions

- [ ] Confirm only current Machine Contract v3 is supported; v1 is obsolete and unknown versions fail closed.
- [ ] Confirm ready Issues without `priority/P0`…`priority/P3` rank after explicit P3.

## Visible outcomes

- [ ] Read the full [`iar-operator` Skill](../../../src/backend/engines/agent_runner/templates/skills/iar-operator/SKILL.md) in the PR and confirm its operation routing, side effects, and daemon lifecycle guidance.
- [ ] Review clean-install and same-name conflict dry-run output in the PR evidence comment; confirm the conflict plan preserves the user-owned Skill.
- [ ] Confirm the queue preview behavior and documented 100-Issue candidate window are acceptable.

Merging the linked PR means accepting these decisions and visible outcomes and authorizing post-merge archival, provided required gates remain green and the merged tree matches the verified tree.
