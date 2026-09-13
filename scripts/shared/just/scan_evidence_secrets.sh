#!/usr/bin/env bash
# scripts/shared/just/scan_evidence_secrets.sh
# Pre-verifier gate for `just ai implement`: fail if an evidence package
# contains a live credential. Runs before the verifier so a leaked token
# is a mechanical failure, never a finding that costs a verification round.
#
# Usage:
#   ./scripts/shared/just/scan_evidence_secrets.sh <evidence-dir>
#
# Optional allowlist: <evidence-dir>/.secret-scan-allow
#   One extended-regex per line. Lines starting with '#' are comments.
#   Use it for deliberate canary strings that tests assert the ABSENCE of.

set -uo pipefail

evidence_dir="${1:-}"

if [ -z "$evidence_dir" ]; then
    echo "Usage: $0 <evidence-dir>"
    exit 2
fi

if [ ! -d "$evidence_dir" ]; then
    echo "ERROR: Evidence directory not found: $evidence_dir"
    exit 2
fi

# Credential shapes worth failing on. Kept narrow on purpose: a noisy scanner
# gets disabled, and a disabled scanner protects nothing.
patterns=(
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'AKIA[0-9A-Z]{16}'
    'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
    '[Bb]earer [A-Za-z0-9._~+/-]{24,}'
    '(secret|token|password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)["'"'"']?\s*[:=]\s*["'"'"']?[A-Za-z0-9._~+/-]{16,}'
    '/(invite|invitations|reset-password|verify-email)/[A-Za-z0-9._~+/-]{20,}'
)

# Values that are already redacted or are obvious placeholders.
redacted_pattern='(\*{3,}|REDACTED|<[a-z-]+>|xxx+|\.\.\.|\$\{[A-Z_]+\}|example|placeholder|dummy)'

allow_file="$evidence_dir/.secret-scan-allow"

if command -v rg &>/dev/null; then
    search() { rg --no-heading --with-filename --line-number --ignore-case "$@"; }
else
    echo "ℹ️  ripgrep not found; falling back to grep -E."
    search() {
        local args=()
        while [ "$#" -gt 0 ]; do
            case "$1" in
                -e) args+=(-e "$2"); shift 2 ;;
                --*|-*) shift ;;
                *) args+=("$1"); shift ;;
            esac
        done
        grep -EnHi -r "${args[@]}"
    }
fi

rg_args=()
for pattern in "${patterns[@]}"; do
    rg_args+=(-e "$pattern")
done

text_files=()
while IFS= read -r -d '' file; do
    text_files+=("$file")
done < <(find "$evidence_dir" -type f \
    \( -name '*.log' -o -name '*.md' -o -name '*.txt' -o -name '*.json' -o -name '*.out' \) \
    -print0 2>/dev/null || true)

hits=""
if [ "${#text_files[@]}" -gt 0 ]; then
    raw_hits="$(search "${rg_args[@]}" "${text_files[@]}" 2>/dev/null || true)"
    if [ -n "$raw_hits" ]; then
        hits="$(printf '%s\n' "$raw_hits" | grep -Ev -i "$redacted_pattern" || true)"
    fi
    if [ -n "$hits" ] && [ -f "$allow_file" ]; then
        while IFS= read -r allow_pattern; do
            case "$allow_pattern" in ''|\#*) continue ;; esac
            hits="$(printf '%s\n' "$hits" | grep -Ev "$allow_pattern" || true)"
        done < "$allow_file"
    fi
fi

# Screenshots cannot be scanned for rendered text, so surface the ones whose
# name suggests a credential surface and let the executor confirm redaction.
suspicious_visuals=()
while IFS= read -r -d '' file; do
    suspicious_visuals+=("$(basename "$file")")
done < <(find "$evidence_dir" -type f \
    \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webm' \) \
    -print0 2>/dev/null | tr '\0' '\n' \
    | grep -Ei 'invit|token|secret|password|reset|credential|auth' \
    | tr '\n' '\0' || true)

if [ "${#suspicious_visuals[@]}" -gt 0 ]; then
    echo "⚠️  Visual evidence that may show a one-time credential (cannot be scanned automatically):"
    printf '   - %s\n' "${suspicious_visuals[@]}"
    echo "   Confirm each one captures a status/state view, not a live link, token, or password."
    echo ""
fi

if [ -n "$hits" ]; then
    echo "❌ Credential-shaped strings found in the evidence package:"
    printf '%s\n' "$hits" | sed 's/^/   /'
    echo ""
    echo "Redact or delete the affected evidence before submitting to the verifier."
    echo "For a deliberate canary string, add its regex to $allow_file"
    exit 1
fi

echo "✅ No credential-shaped strings found in $evidence_dir"
exit 0
