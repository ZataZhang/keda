#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_directory/../../../../" && pwd)"
cd "$repository_root"

tree_hash="$(git rev-parse 'HEAD^{tree}')"
printf 'TREE_HASH=%s\n' "$tree_hash"
printf 'NEGATIVE_CONTROL=Check the baseline configuration guide from HEAD; it must lack the new Gitea registration entry.\n'
set +e
baseline_output="$(git show HEAD:docs/guides/configuration.md | rg -n "agent_runner.gitea_instances" 2>&1)"
baseline_status=$?
set -e
[[ "$baseline_status" == "1" ]]
printf 'EXPECTED_FAIL=baseline guide has no Gitea instance registration entry; rg exit_code=%s\n' \
  "$baseline_status"

export UV_CACHE_DIR=/private/tmp/issue416-uv-cache
printf 'BUILD_COMMAND=uv run --no-sync mkdocs build --strict\n'
uv run --project "$repository_root" --no-sync mkdocs build --strict
printf 'CONFIG_GUIDE_COMMAND=rg -n "agent_runner.gitea_instances" docs/guides/configuration.md\n'
rg -n "agent_runner.gitea_instances" docs/guides/configuration.md
printf 'RUNNER_GUIDE_COMMAND=rg -n -i "Gitea|WIP|Forgejo" docs/guides/agent-runner.md\n'
rg -n -i "Gitea|WIP|Forgejo" docs/guides/agent-runner.md
printf 'OPERATOR_SKILL_COMMAND=rg -n "Gitea|agent_runner.gitea_instances" src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md\n'
rg -n "Gitea|agent_runner.gitea_instances" src/backend/engines/agent_runner/templates/skills/kedacode-operator/SKILL.md
