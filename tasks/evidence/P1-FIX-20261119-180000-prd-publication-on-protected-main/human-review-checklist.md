# Human Review Checklist — Issue #400

## 1. Check the real #398 recovery

**Decision:** Confirm that #398 now points to a PRD commit on `issue-398` and becomes ready only after that commit is published. If this is wrong, the existing Issue may still be unexecutable or may enter the queue before its PRD is available.

**PRD item:** “检查 #398 恢复的实际结果及分支上的 PRD 链接。”

Open [GitHub Issue #398](https://github.com/ZataZhang/keda/issues/398), then run:

```bash
gh issue view 398 --json url,labels,body
```

Check the returned Issue URL, the single `PRD path:` body anchor, the clickable PRD URL's commit on `issue-398`, and the `agent/ready` label after branch publication. Automated RV evidence uses temporary Issue #42 fixtures; it does not substitute for this real Issue check. The implementation work did not read or change #398 or #399.

**Reply:** Respond `同意` if all four values match, or list the exact mismatch that needs follow-up.

**Optional entry:** `just prd review P1-FIX-20261119-180000-prd-publication-on-protected-main.md`.
