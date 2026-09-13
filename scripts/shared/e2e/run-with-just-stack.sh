#!/usr/bin/env bash
# Run Playwright E2E tests locally against a `just run` stack.
#
# Usage:
#   ./scripts/shared/e2e/run-with-just-stack.sh [filter]
#
# Behavior:
#   1. Loads tests/playwright-e2e/.env.e2e.local if present.
#   2. Reads ports from .env.run-state in the repo root (fallback: 8000/5173/3000).
#   3. If backend/admin/public ports are already listening, reuses them.
#   4. Otherwise starts `just run all` in the background and waits for readiness.
#   5. Runs Playwright E2E with PLAYWRIGHT_SKIP_STACK_BOOT=1.
#   6. Tears down the stack with `just down` only if this script started it.
#
# The filter argument is passed through to `pnpm test`, so you can run a single
# spec file, a directory, or a grep tag. Examples:
#   ./scripts/shared/e2e/run-with-just-stack.sh tests/smoke/pages.spec.ts
#   ./scripts/shared/e2e/run-with-just-stack.sh @visual

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"
e2e_root="$repo_root/tests/playwright-e2e"
cd "$repo_root"

# 用与 pydantic-settings 一致的语义读取 env 文件，而不是 shell 的 source。
#
# `.env` / `.env.local` 同时被三类消费方读取，而它们对 `$` 的处理并不一致：
#   - docker compose：`$$` 是转义，容器里拿到 `$`
#   - shell `source`：`$` 会做变量展开（`$2` 变位置参数、`$$` 变 PID），
#     必须彻底避免，否则 bcrypt 哈希会被破坏成非法值
#   - pydantic-settings：按字面量读取，不做任何展开
# 因此仓库约定这类含 `$` 的值统一写成 `$$`（见 docs/guides/configuration.md），
# 本脚本按字面量读出后再把 `$$` 还原为 `$`，与 Docker 侧最终语义对齐；
# 调用方无需再为「shell 会展开」而额外转义。
#
# 支持的语法与 dotenv 对齐：`export KEY=VALUE`、单/双引号包裹、`#` 整行注释。
_load_env_file() {
  local env_file="$1"
  local override_existing_environment="${2:-false}"
  local raw_line env_key env_value env_value_unquoted

  [ -f "$env_file" ] || return 0

  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    # 去掉行尾 CR（Windows 检出的文件）
    raw_line="${raw_line%$'\r'}"
    # 跳过空行与整行注释
    case "$raw_line" in
      ''|'#'*) continue ;;
    esac
    # 允许 `export KEY=VALUE` 形式
    if [[ "$raw_line" == export[[:space:]]* ]]; then
      raw_line="${raw_line#export}"
      raw_line="${raw_line#"${raw_line%%[![:space:]]*}"}"
    fi
    # 不含 `=` 的行不是赋值
    [[ "$raw_line" == *=* ]] || continue

    env_key="${raw_line%%=*}"
    env_value="${raw_line#*=}"
    # key 两侧与 value 前导空白按 dotenv 规则裁剪
    env_key="${env_key%"${env_key##*[![:space:]]}"}"
    env_value="${env_value#"${env_value%%[![:space:]]*}"}"
    # 校验 key 形态，避免把非法标识符 export 进环境
    [[ "$env_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    # 剥离成对引号；单/双引号内都按字面量处理（不做 shell 展开）。
    env_value_unquoted="$env_value"
    case "$env_value" in
      \'*\') env_value_unquoted="${env_value#\'}"; env_value_unquoted="${env_value_unquoted%\'}" ;;
      \"*\") env_value_unquoted="${env_value#\"}"; env_value_unquoted="${env_value_unquoted%\"}" ;;
    esac

    # 还原 Docker 语义的 `$$` -> `$`（仓库约定含 `$` 的值写 `$$`）。
    # 转义反斜杠使模式中的 `$` 被当作字面量，而不是变量引用。
    env_value_unquoted="${env_value_unquoted//\$\$/\$}"

    # 未被本次加载声明过的外部进程环境变量优先；同一批次内后加载的文件
    # 覆盖先加载的（.env -> .env.local -> .env.e2e.local）。
    if [ "$override_existing_environment" != "true" ] \
      && [ -n "${!env_key:-}" ] \
      && ! _was_declared_by_env_file "$env_key"; then
      continue
    fi
    export "${env_key}=${env_value_unquoted}"
    _ENV_FILE_DECLARED_KEYS="${_ENV_FILE_DECLARED_KEYS} ${env_key}"
  done < "$env_file"
}

# 记录本次加载已经写入过的 key（空格分隔）。用字符串而非关联数组，保持
# macOS 自带 bash 3.2 兼容。
_ENV_FILE_DECLARED_KEYS=""

_was_declared_by_env_file() {
  local env_key="$1"
  case " ${_ENV_FILE_DECLARED_KEYS} " in
    *" ${env_key} "*) return 0 ;;
    *) return 1 ;;
  esac
}

# 与 settings.py 的分层一致：先 .env，再 .env.local 覆盖；
# .env.e2e.local 是 E2E 专用覆盖层，最后加载。
_load_env_file "$repo_root/.env"
_load_env_file "$repo_root/.env.local"
_load_env_file "$e2e_root/.env.e2e.local" true

filter="${1:-}"
run_state_file="$repo_root/.env.run-state"
started_by_us="false"

load_run_ports() {
  BACKEND_PORT=8000
  FRONTEND_ADMIN_PORT=5173
  FRONTEND_PUBLIC_PORT=3000
  if [ -f "$run_state_file" ]; then
    # shellcheck disable=SC1090
    source "$run_state_file"
  fi
  # Backward compatibility: legacy run-state used FRONTEND_PORT for admin frontend.
  FRONTEND_ADMIN_PORT="${FRONTEND_ADMIN_PORT:-${FRONTEND_PORT:-5173}}"
}

is_port_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

are_services_running() {
  load_run_ports
  is_port_listening "$BACKEND_PORT" \
    && is_port_listening "$FRONTEND_ADMIN_PORT" \
    && is_port_listening "$FRONTEND_PUBLIC_PORT"
}

is_job_alive() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

wait_for_run_state_file() {
  local just_run_pid="${1:-}"
  local attempts=60
  local i
  for ((i = 0; i < attempts; i++)); do
    if [ -n "$just_run_pid" ] && ! is_job_alive "$just_run_pid"; then
      echo "ERROR: 'just run all' exited before writing $run_state_file" >&2
      return 1
    fi
    if [ -f "$run_state_file" ]; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: timed out waiting for $run_state_file" >&2
  return 1
}

wait_for_ports() {
  local just_run_pid="${1:-}"
  local attempts=120
  local i
  for ((i = 0; i < attempts; i++)); do
    if [ -n "$just_run_pid" ] && ! is_job_alive "$just_run_pid"; then
      echo "ERROR: 'just run all' exited before services became ready" >&2
      return 1
    fi
    if are_services_running; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: timed out waiting for services on ports $BACKEND_PORT/$FRONTEND_ADMIN_PORT/$FRONTEND_PUBLIC_PORT" >&2
  return 1
}

cleanup() {
  if [ "$started_by_us" = "true" ]; then
    echo "Shutting down just-run services..."
    just down || true
  fi
}
trap cleanup EXIT INT TERM

if are_services_running; then
  echo "Services already running (ports $BACKEND_PORT/$FRONTEND_ADMIN_PORT/$FRONTEND_PUBLIC_PORT), reusing them."
else
  echo "Starting services with 'just run all'..."
  just run all &
  just_run_pid=$!
  started_by_us="true"

  wait_for_run_state_file "$just_run_pid"
  load_run_ports
  wait_for_ports "$just_run_pid"
  echo "Services ready on ports $BACKEND_PORT/$FRONTEND_ADMIN_PORT/$FRONTEND_PUBLIC_PORT."
fi

# Point Playwright global setup / tests to the actual ports from .env.run-state.
# Users can still override these via their own environment.
export PLAYWRIGHT_SKIP_STACK_BOOT=1
export PLAYWRIGHT_BASE_URL="${PLAYWRIGHT_BASE_URL:-http://127.0.0.1:$FRONTEND_PUBLIC_PORT}"
# Admin Vite dev server binds to localhost (IPv6 loopback on macOS), so use
# localhost rather than 127.0.0.1 to avoid ERR_CONNECTION_REFUSED.
export PLAYWRIGHT_ADMIN_BASE_URL="${PLAYWRIGHT_ADMIN_BASE_URL:-http://localhost:$FRONTEND_ADMIN_PORT}"
export PLAYWRIGHT_HEALTH_URL="${PLAYWRIGHT_HEALTH_URL:-http://127.0.0.1:$BACKEND_PORT/health}"
export PLAYWRIGHT_API_BASE_URL="${PLAYWRIGHT_API_BASE_URL:-http://127.0.0.1:$BACKEND_PORT}"

# Put this run's artifacts under a single timestamped directory so multiple
# runs do not overwrite each other. Each Playwright worker inherits this env
# var, so all artifacts land in the same directory.
run_timestamp=$(date -u +%Y-%m-%dT%H-%M-%S)
export PLAYWRIGHT_TEST_RESULTS_DIR="${PLAYWRIGHT_TEST_RESULTS_DIR:-$e2e_root/test-results/$run_timestamp}"
export PLAYWRIGHT_JUNIT_OUTPUT_FILE="${PLAYWRIGHT_JUNIT_OUTPUT_FILE:-$PLAYWRIGHT_TEST_RESULTS_DIR/junit.xml}"
echo "E2E artifacts will be written to: $PLAYWRIGHT_TEST_RESULTS_DIR"

cd "$e2e_root"

case "$filter" in
  "")
    pnpm test
    ;;
  headed|headed\ *)
    # Support both `just e2e headed` and `just e2e "headed <file-or-filter>"`.
    headed_filter="${filter#headed}"
    headed_filter="${headed_filter# }"
    if [ -z "$headed_filter" ]; then
      pnpm test:headed
    else
      # shellcheck disable=SC2086
      pnpm test:headed $headed_filter
    fi
    ;;
  smoke)
    pnpm test:smoke
    ;;
  no-auth)
    pnpm test:no-auth
    ;;
  *)
    # Allow flags like `--headed` to be passed through as separate arguments,
    # e.g. `just e2e tests/smoke/public-home.no-auth.spec.ts --headed`.
    # shellcheck disable=SC2086
    pnpm test $filter
    ;;
esac
