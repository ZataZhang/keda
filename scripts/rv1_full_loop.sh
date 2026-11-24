#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
validation_root="/private/tmp/issue416-gitea/rv1"
repository_path="$validation_root/repository"
state_file="/private/tmp/issue416-gitea/rv1-state.json"
evidence_directory="$repository_root/tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client"
cd "$repository_root"

tree_hash="$(git rev-parse 'HEAD^{tree}')"
printf 'TREE_HASH=%s\n' "$tree_hash"
printf 'ENTRY_POINT=RV_CAPTURE_BROWSER=%s bash tasks/evidence/P2-FEAT-20261120-170035-gitea-platform-client/scripts/rv1_full_loop.sh\n' "${RV_CAPTURE_BROWSER:-0}"

bash "$script_directory/gitea_up.sh"
source /private/tmp/issue416-gitea/runtime.env
rm -rf "$validation_root"
mkdir -p "$validation_root/home/.kedacode"
browser_events_file="$validation_root/browser-events.jsonl"
browser_signal_file="$validation_root/wip-captured.txt"
browser_log_file="$validation_root/browser-demo.log"
browser_video_directory="$validation_root/browser-video"
: > "$browser_events_file"
browser_capture_enabled="${RV_CAPTURE_BROWSER:-0}"
if [[ "$browser_capture_enabled" == "1" ]]; then
  # 预置 RV_PLAYWRIGHT_MODULE / RV_BROWSER_ENGINE / RV_FFMPEG 时（如 Linux 复跑容器）
  # 直接使用预装的 Playwright 与 ffmpeg；否则沿用 macOS 下的 webkit 安装流程。
  if [[ -z "${RV_PLAYWRIGHT_MODULE:-}" ]]; then
    export PLAYWRIGHT_BROWSERS_PATH=/private/tmp/issue416-pw-browsers
    export RV_BROWSER_ENGINE=webkit
    if [[ ! -f /private/tmp/issue416-playwright/node_modules/playwright/index.mjs ]]; then
      npm_config_cache=/private/tmp/issue416-npm-cache npm install \
        --prefix /private/tmp/issue416-playwright --no-audit --no-fund playwright@1.50.1
    fi
    if [[ ! -x /private/tmp/issue416-playwright/node_modules/@ffmpeg-installer/darwin-arm64/ffmpeg ]]; then
      npm_config_cache=/private/tmp/issue416-npm-cache npm install \
        --prefix /private/tmp/issue416-playwright --no-audit --no-fund @ffmpeg-installer/darwin-arm64
    fi
    if [[ ! -d "$PLAYWRIGHT_BROWSERS_PATH/webkit-2123" ]]; then
      node /private/tmp/issue416-playwright/node_modules/playwright/cli.js install webkit
    fi
  fi
  node "$script_directory/rv1_browser_demo.mjs" \
    "$RV_GITEA_BASE_URL/rvadmin/keda-rv" \
    "$browser_events_file" \
    "$browser_signal_file" \
    "$evidence_directory" \
    "$browser_video_directory" > "$browser_log_file" 2>&1 &
  browser_capture_pid=$!
else
  printf 'BROWSER_CAPTURE=disabled; see rv-1-browser-capture.txt for the environment blocker.\n'
fi
cat > "$validation_root/home/.kedacode/config.toml" <<EOF
[agent_runner]

[[agent_runner.gitea_instances]]
base_url = "$RV_GITEA_BASE_URL"
token_env = "RV_GITEA_TOKEN"

[agent_runner.autopilot]
enabled = true
merge_check_timeout_seconds = 5

[agent_runner.memory]
enabled = false

[agent_runner.generated_content.issue_from_prompt]
enabled = false
EOF

export HOME="$validation_root/home"
unset KEDACODE_CONFIG
export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
export RV_AGENT_PROBE="$validation_root/agent-probe.jsonl"
export RV_GH_PROBE="$validation_root/gh-probe.log"
export GIT_ASKPASS="$script_directory/git_askpass.sh"
export GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=credential.helper
export GIT_CONFIG_VALUE_0=
export PATH="$script_directory:$PATH"
: > "$RV_AGENT_PROBE"
: > "$RV_GH_PROBE"

printf 'CMD=git clone %s/rvadmin/keda-rv.git %s\n' "$RV_GITEA_BASE_URL" "$repository_path"
git clone "$RV_GITEA_BASE_URL/rvadmin/keda-rv.git" "$repository_path"
cd "$repository_path"

printf 'CMD=uv run --project %s --no-sync kc init --id rv1-gitea --display-name Gitea-RV1 --remote origin --base-branch main\n' "$repository_root"
uv run --project "$repository_root" --no-sync kc init --id rv1-gitea --display-name Gitea-RV1 --remote origin --base-branch main
python3 - "$repository_path/.kedacode.toml" <<'PY'
from pathlib import Path
import re
import sys

config_path = Path(sys.argv[1])
config_text = config_path.read_text(encoding="utf-8")
repository_section = re.search(r"(?ms)^\[agent_runner\.repository\]\s*$.*?(?=^\[|\Z)", config_text)
if repository_section is None:
    config_text = f"{config_text.rstrip()}\n\n[agent_runner.repository]\nplatform = \"gitea\"\n"
else:
    section_text = repository_section.group(0)
    if re.search(r"(?m)^platform\s*=", section_text):
        section_text = re.sub(r"(?m)^platform\s*=.*$", 'platform = "gitea"', section_text)
    else:
        section_text = f'{section_text.rstrip()}\nplatform = "gitea"\n'
    config_text = config_text[: repository_section.start()] + section_text + config_text[repository_section.end() :]
timeout_pattern = re.compile(r"(?m)^merge_check_timeout_seconds\s*=\s*\d+\s*$")
config_text, timeout_replacements = timeout_pattern.subn(
    "merge_check_timeout_seconds = 5", config_text, count=1
)
if timeout_replacements != 1:
    raise SystemExit("Could not set the isolated repository merge check timeout.")
config_path.write_text(config_text, encoding="utf-8")
PY
printf 'REPOSITORY_PLATFORM=gitea\n'
printf 'RV_REPO_MERGE_CHECK_TIMEOUT_SECONDS=5\n'

printf 'CMD=uv run --project %s --no-sync kc labels sync --repo %s\n' "$repository_root" "$repository_path"
uv run --project "$repository_root" --no-sync kc labels sync --repo "$repository_path"
labels_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/labels")"
ready_label_exists="$(printf '%s' "$labels_payload" | jq -r 'any(.[]; .name == "agent/ready")')"
[[ "$ready_label_exists" == "true" ]]
printf 'INDEPENDENT_LABEL_CHECK=agent/ready exists=true\n'
if [[ "$browser_capture_enabled" == "1" ]]; then
  printf '{"stage":"labels"}\n' >> "$browser_events_file"
fi

printf 'CMD=uv run --project %s --no-sync kc issue create --from-prompt <deterministic requirement> --title <title> --type feature --ready --agent codex --repo %s --json\n' "$repository_root" "$repository_path"
issue_create_json="$(uv run --project "$repository_root" --no-sync kc issue create \
  --from-prompt 'Create a small deterministic proof file through the Gitea runner.' \
  --title 'Gitea runner full-loop validation' \
  --type feature --ready --agent codex --repo "$repository_path" --json)"
printf 'ISSUE_CREATE_RESULT=%s\n' "$issue_create_json"
issue_url="$(printf '%s' "$issue_create_json" | jq -r '.created[0].issue_url')"
if [[ ! "$issue_url" =~ /issues/([0-9]+)$ ]]; then
  printf 'ERROR=Could not parse the Issue URL returned by kc issue create.\n' >&2
  exit 1
fi
issue_number="${BASH_REMATCH[1]}"
issue_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/issues/$issue_number")"
issue_ready="$(printf '%s' "$issue_payload" | jq -r 'any(.labels[]; .name == "agent/ready")')"
[[ "$issue_ready" == "true" ]]
printf 'INDEPENDENT_ISSUE_CHECK=issue=%s state=%s ready_label=true\n' \
  "$issue_number" "$(printf '%s' "$issue_payload" | jq -r '.state')"
if [[ "$browser_capture_enabled" == "1" ]]; then
  printf '{"stage":"issue","issue_number":%s}\n' "$issue_number" >> "$browser_events_file"
fi

printf 'CMD=uv run --project %s --no-sync kc run --issue %s --agent codex --repo %s\n' "$repository_root" "$issue_number" "$repository_path"
if run_output="$(uv run --project "$repository_root" --no-sync kc run --issue "$issue_number" \
  --agent codex --repo "$repository_path" 2>&1)"; then
  printf '%s\n' "$run_output"
else
  run_status=$?
  printf '%s\n' "$run_output" >&2
  exit "$run_status"
fi
pulls_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/pulls?state=all")"
pull_url="$(printf '%s' "$pulls_payload" | jq -r --arg head_branch "issue-$issue_number" \
  '[.[] | select(.head.ref == $head_branch) | select((.title // "") | startswith("WIP:"))] | last | .html_url // empty')"
if [[ ! "$pull_url" =~ /pulls?/([0-9]+)$ ]]; then
  printf 'ERROR=Gitea did not list the WIP Pull Request created by kc.\n' >&2
  exit 1
fi
pull_number="${BASH_REMATCH[1]}"
printf 'PULL_URL_SOURCE=independent Gitea GET /pulls after kc run; kc stdout does not emit the create response URL\n'
pull_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/pulls/$pull_number")"
comments_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/issues/$issue_number/comments")"
claim_comment_exists="$(printf '%s' "$comments_payload" | jq -r 'any(.[]; (.body // "") | contains("Agent Runner Claimed"))')"
pull_title="$(printf '%s' "$pull_payload" | jq -r '.title')"
pull_state="$(printf '%s' "$pull_payload" | jq -r '.state')"
pull_merged="$(printf '%s' "$pull_payload" | jq -r '.merged')"
pull_body_links_issue="$(printf '%s' "$pull_payload" | jq -r --arg issue_number "$issue_number" '(.body // "") | contains("#" + $issue_number)')"
[[ "$claim_comment_exists" == "true" ]]
[[ "$pull_title" == "WIP: "* ]]
[[ "$pull_state" == "open" ]]
[[ "$pull_merged" == "false" ]]
[[ "$pull_body_links_issue" == "true" ]]
printf 'INDEPENDENT_CLAIM_CHECK=issue=%s claim_comment=true comments=%s\n' \
  "$issue_number" "$(printf '%s' "$comments_payload" | jq 'length')"
printf 'INDEPENDENT_WIP_CHECK=pr=%s title=%s title_prefix=true body_links_issue=true url=%s\n' \
  "$pull_number" "$pull_title" "$pull_url"
printf '{"issue_number":%s,"pull_number":%s,"pull_url":"%s","repository_url":"%s/rvadmin/keda-rv"}\n' \
  "$issue_number" "$pull_number" "$pull_url" "$RV_GITEA_BASE_URL" > "$state_file"
printf 'WIP_SCREENSHOT_READY=Open %s in Gitea and capture the Draft state.\n' "$pull_url"
if [[ "$browser_capture_enabled" == "1" ]]; then
  printf '{"stage":"wip","issue_number":%s,"pull_url":"%s"}\n' \
    "$issue_number" "$pull_url" >> "$browser_events_file"
  for capture_attempt in $(seq 1 120); do
    if [[ -f "$browser_signal_file" ]]; then
      break
    fi
    if [[ "$capture_attempt" == 120 ]]; then
      cat "$browser_log_file" >&2
      exit 1
    fi
    sleep 0.25
  done
  printf 'WIP_BROWSER_CAPTURE=ready\n'
  cat "$browser_log_file"
else
  printf 'WIP_BROWSER_CAPTURE=blocked; UI screenshots and demo were not produced.\n'
fi

pause_seconds="${RV_WIP_SCREENSHOT_PAUSE_SECONDS:-0}"
if [[ "$pause_seconds" =~ ^[0-9]+$ ]] && (( pause_seconds > 0 )); then
  printf 'WIP_SCREENSHOT_PAUSE_SECONDS=%s\n' "$pause_seconds"
  sleep "$pause_seconds"
fi

head_sha="$(printf '%s' "$pull_payload" | jq -r '.head.sha')"
status_response_path="$validation_root/empty-status.json"
status_http_code="$(curl --silent --show-error \
  --output "$status_response_path" \
  --write-out '%{http_code}' \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/commits/$head_sha/status")"
[[ "$status_http_code" == "200" ]]
empty_status_count="$(jq -r '.total_count' "$status_response_path")"
empty_status_state="$(jq -r '.state' "$status_response_path")"
[[ "$empty_status_count" == "0" ]]
[[ -z "$empty_status_state" ]]
printf 'NEGATIVE_CONTROL=checks_state=unknown http_status=%s total_count=%s\n' \
  "$status_http_code" "$empty_status_count"

printf 'CMD=uv run --project %s --no-sync kc supervise --agent codex --max-issues 1 --repo %s # unknown checks must not merge\n' "$repository_root" "$repository_path"
if supervise_output="$(uv run --project "$repository_root" --no-sync kc supervise --agent codex --max-issues 1 \
  --repo "$repository_path" 2>&1)"; then
  printf '%s\n' "$supervise_output"
else
  supervise_status=$?
  printf '%s\n' "$supervise_output" >&2
  exit "$supervise_status"
fi

unknown_check_pull_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/pulls/$pull_number")"
unknown_check_issue_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/issues/$issue_number")"
unknown_check_merged="$(printf '%s' "$unknown_check_pull_payload" | jq -r '.merged')"
unknown_check_issue_state="$(printf '%s' "$unknown_check_issue_payload" | jq -r '.state')"
[[ "$unknown_check_merged" == "false" ]]
[[ "$unknown_check_issue_state" == "open" ]]
printf 'EXPECTED_FAIL=unknown checks must not merge; observed merged=%s issue_state=%s\n' \
  "$unknown_check_merged" "$unknown_check_issue_state"

status_payload="$(curl --silent --show-error --fail \
  --request POST \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"state":"success","context":"rv1-gitea-check","description":"Deterministic local CI status fixture"}' \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/statuses/$head_sha")"
# Gitea 1.21.11 的 POST /statuses 响应字段为 status，较早补丁版本为 state，两者都接受。
[[ "$(printf '%s' "$status_payload" | jq -r '.status // .state')" == "success" ]]
printf 'GITEA_CI_FIXTURE=status=success context=rv1-gitea-check\n'
printf 'CMD=uv run --project %s --no-sync kc supervise --agent codex --max-issues 1 --repo %s # success may merge\n' "$repository_root" "$repository_path"
if supervise_output="$(uv run --project "$repository_root" --no-sync kc supervise --agent codex --max-issues 1 \
  --repo "$repository_path" 2>&1)"; then
  printf '%s\n' "$supervise_output"
else
  supervise_status=$?
  printf '%s\n' "$supervise_output" >&2
  exit "$supervise_status"
fi

merged_pull_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/pulls/$pull_number")"
closed_issue_payload="$(curl --silent --show-error --fail \
  --header "Authorization: token $RV_GITEA_TOKEN" \
  "$RV_GITEA_BASE_URL/api/v1/repos/rvadmin/keda-rv/issues/$issue_number")"
merged="$(printf '%s' "$merged_pull_payload" | jq -r '.merged')"
merged_at="$(printf '%s' "$merged_pull_payload" | jq -r '.merged_at // empty')"
final_title="$(printf '%s' "$merged_pull_payload" | jq -r '.title')"
issue_state="$(printf '%s' "$closed_issue_payload" | jq -r '.state')"
final_title_has_wip_prefix="$(printf '%s' "$merged_pull_payload" | jq -r '(.title // "") | startswith("WIP: ")')"
[[ "$merged" == "true" ]]
[[ -n "$merged_at" ]]
[[ "$final_title_has_wip_prefix" == "false" ]]
[[ "$issue_state" == "closed" ]]
printf 'INDEPENDENT_READY_CHECK=pr=%s title_has_wip_prefix=%s title=%s\n' \
  "$pull_number" "$final_title_has_wip_prefix" "$final_title"
printf 'INDEPENDENT_MERGED_CHECK=pr=%s merged=%s merged_at=%s\n' "$pull_number" "$merged" "$merged_at"
printf 'INDEPENDENT_ISSUE_CLOSED_CHECK=issue=%s state=closed\n' "$issue_number"

gh_invocation_count="$(wc -l < "$RV_GH_PROBE" | tr -d '[:space:]')"
agent_invocation_count="$(wc -l < "$RV_AGENT_PROBE" | tr -d '[:space:]')"
[[ "$gh_invocation_count" == "0" ]]
[[ "$agent_invocation_count" -gt 0 ]]
printf 'EXECUTOR_PROBE=codex_invocations=%s gh_invocations=%s\n' \
  "$agent_invocation_count" "$gh_invocation_count"
if [[ "$browser_capture_enabled" == "1" ]]; then
  printf '{"stage":"merged","issue_number":%s,"pull_url":"%s"}\n' \
    "$issue_number" "$pull_url" >> "$browser_events_file"
  wait "$browser_capture_pid"
  cat "$browser_log_file"
  browser_video_path="$(sed -n 's/^BROWSER_VIDEO_WEBM=//p' "$browser_log_file" | tail -n 1)"
  if [[ -z "$browser_video_path" || ! -f "$browser_video_path" ]]; then
    printf 'ERROR=browser demo recording was not produced.\n' >&2
    exit 1
  fi
  ffmpeg_path="${RV_FFMPEG:-/private/tmp/issue416-playwright/node_modules/@ffmpeg-installer/darwin-arm64/ffmpeg}"
  "$ffmpeg_path" -y -i "$browser_video_path" -c:v libx264 -preset veryfast -crf 22 \
    -pix_fmt yuv420p -movflags +faststart "$evidence_directory/rv-1-demo.mp4" >/dev/null 2>&1
  printf 'PRIMARY_DEMO=rv-1-demo.mp4 duration_shown_per_step_seconds>=3\n'
fi
printf 'RESULT=PASS\n'
