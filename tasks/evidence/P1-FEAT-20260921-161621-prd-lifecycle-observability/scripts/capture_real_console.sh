#!/usr/bin/env bash
# rv-1 / rv-2 真实入口证据采集驱动脚本。
#
# 在完全隔离的临时 HOME + IAR_CONFIG 下：
#   1. 建一个一次性 git 仓库（含 .iar.toml 与一份 PRD）；
#   2. 把 PRD 生命周期账本直接种进临时 console.db（真实 schema + 真实写入函数）；
#   3. 同步 frontend-public 静态产物到 backend static/console；
#   4. 启动真实 uvicorn（同一进程同时提供静态前端与真实 FastAPI + SQLite）；
#   5. 用真实 Chromium 走 Roadmap 详情与 Stats 页面并留存截图 + API JSON。
#
# 不触碰用户 ~/.iar、console.db 或交付仓库。

set -euo pipefail

WT=/Users/zata/code/keda-worktrees/prd-lifecycle-observability
STEM=P1-FEAT-20260921-161621-prd-lifecycle-observability
PORT=${PORT:-8899}
BASE_URL="http://127.0.0.1:${PORT}"
OUT="$WT/tasks/evidence/$STEM"
WORK=$(mktemp -d /tmp/keda-lifecycle-rv.XXXXXX)
HOME_REAL="${HOME}"
export HOME="$WORK/home"
mkdir -p "$HOME" "$WORK/bin"

# 假 gh：让 roadmap 的状态解析拿到空列表而不是真的调用 GitHub。
cat > "$WORK/bin/gh" <<'GH'
#!/usr/bin/env bash
case "$*" in
  *"api"*) printf '[]\n' ;;
  *"pr list"*) printf '[]\n' ;;
  *"issue list"*) printf '[]\n' ;;
  *) printf '\n' ;;
esac
exit 0
GH
chmod +x "$WORK/bin/gh"
export PATH="$WORK/bin:$PATH"
export IAR_SKIP_GH_AUTH_CHECK=1

# 1. 一次性 fixture 仓库
REPO="$WORK/fixture-repo"
mkdir -p "$REPO/tasks/pending"
cat > "$REPO/tasks/pending/P1-FEAT-20260921-161621-demo-lifecycle.md" <<'PRD'
# PRD: Demo Lifecycle Observability

## Acceptance Checklist
- [ ] item one
- [ ] item two
PRD
cat > "$REPO/.iar.toml" <<'TOML'
[agent_runner.autopilot]
enabled = false
TOML
git -C "$REPO" init -q
git -C "$REPO" add -A
git -C "$REPO" -c user.email=rv@example.com -c user.name=rv commit -qm "fixture: seed demo PRD"

DB="$WORK/console.db"
cat > "$WORK/config.toml" <<TOML
[agent_runner.console]
history_db_path = "$DB"
process_registry_path = "$WORK/processes.json"
process_log_dir = "$WORK/logs"

[agent_runner.repositories.keda-main]
path = "$REPO"
display_name = "Fixture Repo"
enabled = true
TOML
export IAR_CONFIG="$WORK/config.toml"

# 2. 种账本（真实 store + 真实 record 函数）
cd "$WT"
uv run python - "$DB" <<'PY'
import sys
from pathlib import Path
from backend.infrastructure.persistence.console_store import SqliteConsoleStore
from backend.core.use_cases.agent_runner_lifecycle import (
    LifecycleEventType,
    record_lifecycle_event,
    record_lifecycle_terminal,
)

db_path = sys.argv[1]
store = SqliteConsoleStore(db_path)
REPO = "keda-main"
PRD = "tasks/pending/P1-FEAT-20260921-161621-demo-lifecycle.md"

def ev(issue, event_type, ts, **kw):
    record_lifecycle_event(
        store=store, repo_id=REPO, prd_path=PRD, issue_number=issue,
        trigger="console_start", event_type=event_type, actor="runner",
        occurred_at=ts, **kw,
    )

# 进行中的 PRD：失败 attempt → 重试 → 恢复 → 阻塞 → 解除 → 验证失败 → 审阅中，
# 历史全部保留；所有时间戳都早于“当前时刻”，端到端才会计算到 now。
ev(161, LifecycleEventType.QUEUED, "2026-09-21T06:00:00+00:00", event_key="q161")
ev(161, LifecycleEventType.STARTED, "2026-09-21T06:01:00+00:00", event_key="s161")
ev(161, LifecycleEventType.CLAIMED, "2026-09-21T06:02:00+00:00", event_key="c161",
   detail={"agent": "codex", "issue_kind": "ready"})
ev(161, LifecycleEventType.ATTEMPT, "2026-09-21T06:03:00+00:00", event_key="a161-1",
   detail={"agent": "codex", "attempt_number": 1, "failure_type": "verification",
           "recovered": False, "duration_seconds": 90.0})
ev(161, LifecycleEventType.RETRY, "2026-09-21T06:05:00+00:00", event_key="r161-2",
   detail={"agent": "codex", "attempt_number": 2})
ev(161, LifecycleEventType.RECOVERED, "2026-09-21T06:12:00+00:00", event_key="rec161-2",
   detail={"agent": "codex", "attempt_number": 2})
ev(161, LifecycleEventType.BLOCKED, "2026-09-21T06:20:00+00:00", event_key="b161",
   detail={"error_summary": "等待人工澄清 PRD 验收口径"})
ev(161, LifecycleEventType.UNBLOCKED, "2026-09-21T06:30:00+00:00", event_key="ub161",
   detail={"reason": "人工已澄清"})
ev(161, LifecycleEventType.IMPLEMENTATION_COMPLETED, "2026-09-21T06:40:00+00:00", event_key="ic161")
ev(161, LifecycleEventType.VALIDATION_STARTED, "2026-09-21T06:42:00+00:00", event_key="vs161",
   detail={"head_sha": "abc123", "total": 6})
ev(161, LifecycleEventType.VALIDATION_FAILED, "2026-09-21T06:50:00+00:00", event_key="vf161",
   detail={"head_sha": "abc123", "unchecked_count": 2})
ev(161, LifecycleEventType.REVIEW_STARTED, "2026-09-21T06:55:00+00:00", event_key="rv161",
   detail={"outcome": "waiting_for_checks"})

# 三个已完成 PRD（供 Stats 分位数与明细）：含执行段、审阅段与一次阻塞段。
def completed(issue, queued, claimed, review_started, merged, prd_suffix, blocked=None):
    prd_path = f"tasks/pending/P0-FEAT-2026{prd_suffix}.md"
    for event_type, ts, key in (
        (LifecycleEventType.QUEUED, queued, f"q{issue}"),
        (LifecycleEventType.CLAIMED, claimed, f"c{issue}"),
        (LifecycleEventType.REVIEW_STARTED, review_started, f"rv{issue}"),
    ):
        record_lifecycle_event(store=store, repo_id=REPO, prd_path=prd_path, issue_number=issue,
                               trigger="console_start", event_type=event_type,
                               actor="runner", occurred_at=ts, event_key=key)
    if blocked is not None:
        for event_type, ts, key in (
            (LifecycleEventType.BLOCKED, blocked[0], f"b{issue}"),
            (LifecycleEventType.UNBLOCKED, blocked[1], f"ub{issue}"),
        ):
            record_lifecycle_event(store=store, repo_id=REPO, prd_path=prd_path, issue_number=issue,
                                   trigger="console_start", event_type=event_type,
                                   actor="runner", occurred_at=ts, event_key=key)
    record_lifecycle_terminal(store=store, repo_id=REPO, prd_path=prd_path, issue_number=issue,
                              trigger="console_start", event_type=LifecycleEventType.MERGED,
                              outcome="completed", actor="merge_queue", occurred_at=merged,
                              event_key=f"m{issue}")

completed(171, "2026-09-18T08:00:00+00:00", "2026-09-18T08:01:00+00:00",
          "2026-09-18T08:05:00+00:00", "2026-09-18T08:10:00+00:00", "0918-1")
completed(172, "2026-09-19T08:00:00+00:00", "2026-09-19T08:02:00+00:00",
          "2026-09-19T08:15:00+00:00", "2026-09-19T08:20:00+00:00", "0919-1",
          blocked=("2026-09-19T08:05:00+00:00", "2026-09-19T08:10:00+00:00"))
completed(173, "2026-09-20T08:00:00+00:00", "2026-09-20T08:01:00+00:00",
          "2026-09-20T08:20:00+00:00", "2026-09-20T08:30:00+00:00", "0920-1")
print("seeded lifecycle ledger at", db_path)
PY

# 3. 同步前端静态产物（默认执行；已同步过可 SKIP_CONSOLE_SYNC=1 跳过，
#    避免在临时 HOME 下重复触发 pnpm 构建）。
if [ "${SKIP_CONSOLE_SYNC:-0}" != "1" ]; then
  just console-sync
fi

# 4. 启动真实后端（静态前端 + API + SQLite 同源）
uv run uvicorn backend.api.app:app --host 127.0.0.1 --port "$PORT" \
  > "$WORK/uvicorn.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
  if curl -sf "$BASE_URL/api/v1/agent-runner/health" > /dev/null; then break; fi
  sleep 1
done
curl -sf "$BASE_URL/api/v1/agent-runner/health" > /dev/null || {
  echo "backend failed to become ready"; tail -40 "$WORK/uvicorn.log"; exit 1;
}

# 5. 真实 Chromium 采集
export PLAYWRIGHT_BROWSERS_PATH="$HOME_REAL/Library/Caches/ms-playwright"
node "$OUT/scripts/capture_real_console.mjs" "$BASE_URL" "$OUT"

echo "done. artifacts in $OUT"
echo "workdir preserved at $WORK (contains config.toml + console.db)"
