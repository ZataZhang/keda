#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  *Username*) printf '%s\n' "$RV_GITEA_USERNAME" ;;
  *Password*) printf '%s\n' "$RV_GITEA_TOKEN" ;;
  *) exit 1 ;;
esac
