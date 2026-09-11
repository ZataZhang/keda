#!/usr/bin/env bash
# kedacode / iar CLI installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ZataZhang/keda/main/install.sh | bash
#   curl -fsSL ... | bash -s -- --version v0.2.0
#   curl -fsSL ... | bash -s -- --source pypi
#   bash install.sh --check
#   bash install.sh --uninstall
#
# Behaviour:
#   * Detects host OS (macOS / Linux) and Python >= 3.11.
#   * Prefers `uv` (bootstrap if missing), then `pipx`, then `pip --user`.
#   * Installs from the GitHub Release tarball by default (--source auto).
#   * --source pypi installs the published PyPI package `kedacode`（命令名仍是 iar）；
#     PyPI 不可达或版本不存在时直接报错，绝不静默回退到 tarball。
#   * Verifies `iar --version` exits 0 and emits a clear PATH hint if needed.
#   * Refuses to use sudo; never touches system package managers.
#
# Environment overrides:
#   KEDA_VERSION       Tag to install (default: latest non-draft release).
#   KEDA_SOURCE=auto|pypi|tarball  Install source (default: auto = GitHub tarball).
#   KEDA_PYPI=1        Legacy alias for `--source pypi` (kept for backward compatibility).
#   KEDA_INSTALL_METHOD=uv|pipx|pip  Force installer selection.

set -euo pipefail

readonly REPO_SLUG="${KEDA_REPO:-ZataZhang/keda}"
readonly PYPI_INDEX_URL="https://pypi.org/pypi"
# 改名前的旧分发包名。它和 kedacode 都提供 iar 可执行文件，uv 拒绝让不同包
# 覆盖同名 binary，因此装过旧包的用户必须先卸载（见 check_legacy_tool_conflict）。
readonly LEGACY_TOOL_NAME="keda"
readonly PY_MIN_MAJOR=3
readonly PY_MIN_MINOR=11
# 分发包名的单一声明处（PRD FR-1）：PyPI 包名是 kedacode，命令名是 iar。
readonly DEFAULT_TOOL_NAME="kedacode"
readonly TOOL_BIN_NAME="iar"

INSTALL_METHOD="${KEDA_INSTALL_METHOD:-}"
VERSION_TAG="${KEDA_VERSION:-}"
SOURCE="${KEDA_SOURCE:-auto}"
if [ "${KEDA_PYPI:-0}" = "1" ]; then
    SOURCE="pypi"
fi
UNINSTALL_ONLY=0
CHECK_ONLY=0
SHORT_HELP=0

log_info() { printf '\033[36m[install]\033[0m %s\n' "$*"; }
log_warn() { printf '\033[33m[install]\033[0m %s\n' "$*" >&2; }
log_err()  { printf '\033[31m[install]\033[0m %s\n' "$*" >&2; }

show_help() {
    cat <<'EOF'
kedacode / iar installer

Usage: install.sh [options]
  --version <tag>     Install a specific release tag (default: latest).
  --method uv|pipx|pip  Force a specific installer.
  --source auto|pypi|tarball  Install source (default: auto = GitHub tarball).
  --check             Dry-run; print the plan without writing anything.
  --uninstall         Remove the kedacode tool environment and the iar binary.
  -h, --help          Show this help.

Environment:
  KEDA_VERSION         Same as --version.
  KEDA_SOURCE=auto|pypi|tarball  Same as --source.
  KEDA_PYPI=1          Legacy alias for `--source pypi`.
  KEDA_INSTALL_METHOD  Same as --method.
EOF
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --version) VERSION_TAG="${2:-}"; shift 2 ;;
            --version=*) VERSION_TAG="${1#*=}"; shift ;;
            --method) INSTALL_METHOD="${2:-}"; shift 2 ;;
            --method=*) INSTALL_METHOD="${1#*=}"; shift ;;
            --source) SOURCE="${2:-}"; shift 2 ;;
            --source=*) SOURCE="${1#*=}"; shift ;;
            --check) CHECK_ONLY=1; shift ;;
            --uninstall) UNINSTALL_ONLY=1; shift ;;
            -h|--help) show_help; exit 0 ;;
            *) log_err "Unknown option: $1"; show_help; exit 2 ;;
        esac
    done
}

detect_os() {
    case "$(uname -s)" in
        Darwin) HOST_OS="darwin" ;;
        Linux)  HOST_OS="linux" ;;
        *) log_err "Unsupported OS: $(uname -s)"; exit 1 ;;
    esac
    case "$(uname -m)" in
        arm64|aarch64) HOST_ARCH="arm64" ;;
        x86_64|amd64)  HOST_ARCH="x86_64" ;;
        *) log_err "Unsupported architecture: $(uname -m)"; exit 1 ;;
    esac
}

# 探测 ambient python3 但**不致命**：uv 路径自带解释器，压根不需要它。
#
# 这里曾经直接 exit 1，导致 README 顶部那条 `curl ... | bash` 在原装 macOS 上
# 必然失败——系统自带的 /usr/bin/python3 是 3.9.x，而门禁在 bootstrap uv 之前
# 就把脚本杀掉了。CI 没暴露是因为 install-smoke 先跑 actions/setup-python。
#
# 结果写入三个变量：PY_BIN（可能为空）、PY_VERSION（展示用）、
# PY_OK（1=满足最低版本，0=过旧或缺失）。真正需要 ambient 解释器的只有
# pipx / pip 两条路径，版本门禁因此下移到 resolve_installer。
detect_python() {
    PY_BIN=""
    PY_VERSION="(not found)"
    PY_OK=0
    if ! command -v python3 >/dev/null 2>&1; then
        return
    fi
    PY_BIN="$(command -v python3)"
    PY_VERSION="$("$PY_BIN" -c 'import sys;print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "(unknown)")"
    if "$PY_BIN" -c "import sys;sys.exit(0 if sys.version_info>=(${PY_MIN_MAJOR},${PY_MIN_MINOR}) else 1)" 2>/dev/null; then
        PY_OK=1
    fi
}

# 旧包名 keda 与新包名 kedacode 都注册 iar，uv 会以
#   error: Executable already exists: iar (use `--force` to overwrite)
# 失败。那句报错既没说清冲突来自哪个包，也没说该怎么办，所以这里提前拦下
# 并给出确切命令。--check 模式只告警不退出，让 dry-run 能完整打印计划。
check_legacy_tool_conflict() {
    [ "$INSTALL_METHOD" = "uv" ] || return 0
    command -v uv >/dev/null 2>&1 || return 0
    uv tool list 2>/dev/null | grep -q "^${LEGACY_TOOL_NAME} " || return 0

    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_warn "Legacy tool '${LEGACY_TOOL_NAME}' is installed and owns the 'iar' executable; the real install would fail until it is removed."
        return 0
    fi
    log_err "Legacy tool '${LEGACY_TOOL_NAME}' is installed and owns the 'iar' executable."
    log_err "Both packages register 'iar', so uv refuses to overwrite it. Remove the old one first:"
    log_err "    uv tool uninstall ${LEGACY_TOOL_NAME}"
    log_err "    curl -fsSL https://raw.githubusercontent.com/${REPO_SLUG}/main/install.sh | bash -s -- --source pypi"
    log_err "If that entry is your editable development install of this repository, keep it and skip this installer."
    exit 1
}

resolve_installer() {
    if [ -n "$INSTALL_METHOD" ]; then
        case "$INSTALL_METHOD" in
            uv|pipx|pip) ;;
            *) log_err "Unknown --method: $INSTALL_METHOD"; exit 2 ;;
        esac
    elif command -v uv >/dev/null 2>&1; then
        INSTALL_METHOD="uv"
    elif [ "$PY_OK" -eq 1 ] && command -v pipx >/dev/null 2>&1; then
        INSTALL_METHOD="pipx"
    elif [ "$PY_OK" -eq 1 ]; then
        INSTALL_METHOD="pip"
    else
        # ambient python 过旧或缺失时选 uv：它会被 bootstrap 并自带解释器，
        # 而 pipx / pip 只能用 ambient 那个，必然装不上。
        log_info "Ambient python3 is ${PY_VERSION} (need >= ${PY_MIN_MAJOR}.${PY_MIN_MINOR}); using uv, which brings its own interpreter."
        INSTALL_METHOD="uv"
    fi
    # 版本门禁只对真正使用 ambient 解释器的两条路径生效。
    if [ "$INSTALL_METHOD" != "uv" ] && [ "$PY_OK" -ne 1 ]; then
        log_err "Python ${PY_VERSION} cannot run ${DEFAULT_TOOL_NAME} (need >= ${PY_MIN_MAJOR}.${PY_MIN_MINOR}) and --method ${INSTALL_METHOD} uses it directly."
        log_err "Either drop --method so the installer picks uv, pass --method uv explicitly, or install Python >= ${PY_MIN_MAJOR}.${PY_MIN_MINOR} first."
        exit 1
    fi
}

bootstrap_uv() {
    log_info "uv not found; bootstrapping via astral.sh/install.sh"
    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_info "[check] would run: curl -LsSf https://astral.sh/uv/install.sh | sh"
        INSTALL_METHOD="uv"
        return
    fi
    curl -LsSf --max-time 60 https://astral.sh/uv/install.sh | sh >/dev/null
    # shellcheck disable=SC1091
    if [ -f "$HOME/.local/bin/env" ]; then
        . "$HOME/.local/bin/env"
    fi
    if ! command -v uv >/dev/null 2>&1; then
        log_err "uv bootstrap failed; please install uv manually: https://docs.astral.sh/uv/"
        exit 1
    fi
    INSTALL_METHOD="uv"
}

resolve_version() {
    if [ -n "$VERSION_TAG" ]; then
        return
    fi
    if [ "$CHECK_ONLY" -eq 1 ]; then
        VERSION_TAG="<latest>"
        return
    fi
    local latest
    if ! latest="$(curl -fsSL --max-time 30 "https://api.github.com/repos/${REPO_SLUG}/releases/latest" \
        | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"; then
        log_warn "Could not determine latest release; falling back to install from main branch."
        VERSION_TAG="main"
    else
        VERSION_TAG="$latest"
    fi
}

tarball_url() {
    case "$SOURCE" in
        auto|tarball)
            # 无 release 时的 fallback 会把 VERSION_TAG 置为 main（分支而非 tag），
            # 必须拼 archive/refs/heads/main.tar.gz（PRD FR-12）；其余按 tag 拼 refs/tags。
            if [ "$VERSION_TAG" = "main" ]; then
                printf 'https://github.com/%s/archive/refs/heads/main.tar.gz' "$REPO_SLUG"
            else
                printf 'https://github.com/%s/archive/refs/tags/%s.tar.gz' "$REPO_SLUG" "$VERSION_TAG"
            fi
            ;;
        *)
            log_err "Unknown --source: $SOURCE (expected auto|pypi|tarball)"
            exit 2
            ;;
    esac
}

pypi_requirement() {
    # 输出 pip 风格的 PyPI 安装需求；--version 的 v* tag / 语义化版本映射为精确 pin。
    # 非检查模式下先验证 PyPI 可达与版本存在，失败即报错退出（PRD FR-8）。
    local pinned_version
    case "$VERSION_TAG" in
        v[0-9]*)              pinned_version="${VERSION_TAG#v}" ;;
        [0-9]*.[0-9]*.[0-9]*) pinned_version="$VERSION_TAG" ;;
        *)                    pinned_version="" ;;
    esac
    if [ "$CHECK_ONLY" -eq 1 ]; then
        if [ -n "$pinned_version" ]; then
            printf '%s==%s' "$DEFAULT_TOOL_NAME" "$pinned_version"
        else
            printf '%s' "$DEFAULT_TOOL_NAME"
        fi
        return
    fi
    if ! curl -fsSL --max-time 30 "${PYPI_INDEX_URL}/${DEFAULT_TOOL_NAME}/json" >/dev/null 2>&1; then
        log_err "PyPI 上找不到 ${DEFAULT_TOOL_NAME}（${PYPI_INDEX_URL}/${DEFAULT_TOOL_NAME}/json 不可达）。"
        log_err "--source pypi 不会静默回退到 GitHub tarball；请确认包已发布，或改用 --source auto。"
        exit 1
    fi
    if [ -n "$pinned_version" ] \
        && ! curl -fsSL --max-time 30 "${PYPI_INDEX_URL}/${DEFAULT_TOOL_NAME}/${pinned_version}/json" >/dev/null 2>&1; then
        log_err "PyPI 上不存在版本 ${pinned_version}（${PYPI_INDEX_URL}/${DEFAULT_TOOL_NAME}/${pinned_version}/json 返回 404）。"
        exit 1
    fi
    if [ -n "$pinned_version" ]; then
        printf '%s==%s' "$DEFAULT_TOOL_NAME" "$pinned_version"
    else
        printf '%s' "$DEFAULT_TOOL_NAME"
    fi
}

source_label() {
    case "$SOURCE" in
        pypi) printf 'pypi:%s' "$DEFAULT_TOOL_NAME" ;;
        auto|tarball) tarball_url ;;
        *) printf 'unknown:%s' "$SOURCE" ;;
    esac
}

print_plan() {
    cat <<EOF
[install] plan:
  os:        ${HOST_OS}/${HOST_ARCH}
  python:    ${PY_VERSION}
  method:    ${INSTALL_METHOD}
  version:   ${VERSION_TAG:-<unset>}
  source:    $(source_label)
  tool:      ${DEFAULT_TOOL_NAME} (binary: ${TOOL_BIN_NAME})
EOF
}

run_install() {
    local install_target
    case "$SOURCE" in
        pypi)         install_target="$(pypi_requirement)" ;;
        auto|tarball) install_target="$(tarball_url)" ;;
        *)
            log_err "Unknown --source: $SOURCE (expected auto|pypi|tarball)"
            exit 2
            ;;
    esac
    log_info "Installing ${DEFAULT_TOOL_NAME} from ${install_target}"
    case "$INSTALL_METHOD" in
        uv)
            if ! command -v uv >/dev/null 2>&1; then bootstrap_uv; fi
            if [ "$CHECK_ONLY" -eq 1 ]; then
                log_info "[check] would run: uv tool install ${install_target}"
                return
            fi
            uv tool install --reinstall "$install_target"
            ;;
        pipx)
            if [ "$CHECK_ONLY" -eq 1 ]; then
                log_info "[check] would run: pipx install ${install_target}"
                return
            fi
            pipx install --force "$install_target"
            ;;
        pip)
            if [ "$CHECK_ONLY" -eq 1 ]; then
                log_info "[check] would run: python3 -m pip install --user ${install_target}"
                return
            fi
            "$PY_BIN" -m pip install --user --upgrade "$install_target"
            ;;
    esac
}

run_uninstall() {
    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_info "[check] would remove tool env + iar binary"
        return
    fi
    case "$INSTALL_METHOD" in
        uv)    uv tool uninstall "$DEFAULT_TOOL_NAME" >/dev/null 2>&1 || true ;;
        pipx)  pipx uninstall "$DEFAULT_TOOL_NAME" >/dev/null 2>&1 || true ;;
        pip)   "$PY_BIN" -m pip uninstall -y "$DEFAULT_TOOL_NAME" >/dev/null 2>&1 || true ;;
    esac
    rm -f "$HOME/.local/bin/${TOOL_BIN_NAME}"
    log_info "Uninstall complete."
}

verify_iar() {
    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_info "[check] would run: ${TOOL_BIN_NAME} --version"
        return
    fi
    if ! command -v "$TOOL_BIN_NAME" >/dev/null 2>&1; then
        log_err "Install reported success but '${TOOL_BIN_NAME}' is not on PATH."
        log_err "Add ~/.local/bin to your PATH (or restart the shell) and retry."
        exit 1
    fi
    if ! "$TOOL_BIN_NAME" --version >/dev/null 2>&1; then
        log_err "'${TOOL_BIN_NAME} --version' failed; the install may be incomplete."
        exit 1
    fi
    log_info "$($TOOL_BIN_NAME --version)"
}

main() {
    parse_args "$@"
    detect_os
    detect_python
    resolve_installer
    check_legacy_tool_conflict
    resolve_version

    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_info "Dry-run; no changes will be made."
        print_plan
        return
    fi

    if [ "$UNINSTALL_ONLY" -eq 1 ]; then
        run_uninstall
        return
    fi

    log_info "Selected installer: ${INSTALL_METHOD}; source: $(source_label)"
    run_install
    verify_iar
    log_info "Done. Run \`iar init\` inside a Git repository to start."
}

main "$@"
