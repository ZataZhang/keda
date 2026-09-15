#!/usr/bin/env bash
# Create a new Alembic migration script whose filename follows the repository's
# existing convention (YYYYMMDD<sep>HHMMSS<sep><slug>.py).
#
# This script enforces the migration naming convention documented in
# docs/database/migrations.md. It only generates the file shell; the developer
# (or AI agent) is responsible for filling in upgrade() / downgrade() and the
# docstring.

set -euo pipefail

show_usage() {
    cat <<'EOF'
Usage:
  scripts/shared/alembic/new_migration.sh <slug> [<alembic-versions-dir>]

Arguments:
  slug                  Required. Lowercase snake_case describing the migration,
                        e.g. add_trace_tables, drop_legacy_session_index.
  alembic-versions-dir  Optional. Defaults to ./alembic/versions relative to
                        the current working directory.

Behavior:
  - Rejects slugs that are empty, contain non-[a-z0-9_] characters, or start
    with a digit (Python identifier rules).
  - Refuses to run when alembic heads reports zero or multiple heads; the
    version graph must be a single linear head.
  - Invokes `alembic revision -m <slug>` so the body matches script.py.mako,
    then renames the generated file to the repository's naming convention and
    rewrites the docstring header to match the file.
  - Leaves the `revision` variable as Alembic generated it, unless the
    repository's existing migrations consistently use the filename timestamp
    prefix as the revision; in that case the rewritten revision keeps the
    separator style of the existing revisions ('-' and '_' are both seen in
    the wild, including mixed with the filename separator).
  - Does NOT auto-fill upgrade() / downgrade(); the caller edits those by hand.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    show_usage
    exit 0
fi

slug="${1:-}"
versions_dir="${2:-./alembic/versions}"

if [ -z "$slug" ]; then
    echo "ERROR: missing <slug> argument." >&2
    show_usage >&2
    exit 2
fi

if ! [[ "$slug" =~ ^[a-z][a-z0-9_]*$ ]]; then
    echo "ERROR: slug must match ^[a-z][a-z0-9_]*\$ (got: '$slug')." >&2
    exit 2
fi

if [ ! -d "$versions_dir" ]; then
    echo "ERROR: alembic versions directory not found: $versions_dir" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is required on PATH." >&2
    exit 2
fi

# Enforce unique head before creating a new revision.
heads_output="$(uv run alembic heads 2>/dev/null || true)"
head_count="$(printf "%s\n" "$heads_output" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
if [ "$head_count" -ne 1 ]; then
    echo "ERROR: alembic version graph must have exactly 1 head before creating a new migration." >&2
    echo "       Current heads:" >&2
    printf "%s\n" "$heads_output" >&2
    exit 3
fi

# `alembic heads` 的输出形如 "cdcecd56387a (head)"，要把标注剥掉再用：
# 带着 " (head)" 写进 Revises 文档串，照它去 `alembic downgrade` 会找不到那个 revision。
current_head="$(printf "%s\n" "$heads_output" | sed '/^[[:space:]]*$/d' | head -n 1 |
    awk '{print $1}')"

# 文件名分隔符必须跟着项目的既有约定走，不能写死。
#
# 这里曾经硬编码 '_'，而仓库的约定由两处共同定义、都可能是 '-'：alembic.ini 的
# file_template（alembic 自己按它命名），以及 hooks/shared/check_schema_conventions.py
# 的多数派探测（它扫 versions 目录里已有的文件）。硬编码的一方与这两处不一致时，
# 每次 `just new-migration` 都会产出一个注定过不了门禁的文件名，而且因为
# pre-commit 只把新文件本身交给探测器（单文件里 '_' 自然是多数派），错误要等到
# 全量守卫测试才暴露，离生成它的命令已经很远。
#
# 探测顺序：已有迁移文件的多数派 > alembic.ini 的 file_template > 默认 '-'。
# 已有文件优先，是因为门禁最终就是按它们探测的。
detect_separator() {
    local dash_count underscore_count template_line
    dash_count="$(ls "$versions_dir" 2>/dev/null | grep -cE '^[0-9]{8}-[0-9]{6}-' || true)"
    underscore_count="$(ls "$versions_dir" 2>/dev/null | grep -cE '^[0-9]{8}_[0-9]{6}_' || true)"
    if [ "$dash_count" -gt "$underscore_count" ]; then
        printf '%s' '-'
        return
    fi
    if [ "$underscore_count" -gt "$dash_count" ]; then
        printf '%s' '_'
        return
    fi
    # 平票（含两者都为 0 的首次迁移）时看 alembic.ini：取 date 段与 time 段之间那个字符。
    template_line="$(grep -E '^[[:space:]]*file_template[[:space:]]*=' alembic.ini 2>/dev/null || true)"
    case "$template_line" in
        *'day).2d_'*) printf '%s' '_' ;;
        *'day).2d-'*) printf '%s' '-' ;;
        *) printf '%s' '-' ;;
    esac
}

separator="$(detect_separator)"
timestamp="$(date "+%Y%m%d${separator}%H%M%S")"
target_filename="${timestamp}${separator}${slug}.py"
target_path="${versions_dir%/}/${target_filename}"

# 读出迁移文件里 `revision` 变量的值。
# script.py.mako 用单引号，手写的迁移多用双引号——两种都要认，否则会把整行
# 当成 revision 值塞回去。
read_revision_value() {
    grep -E '^revision: str = ' "$1" 2>/dev/null | head -n 1 |
        sed -E "s|^revision: str = ['\"]([^'\"]*)['\"].*|\1|" || true
}

# revision 用哪套约定（alembic 自动生成的 hex，还是文件名的时间戳前缀），按
# versions 目录里既有迁移的多数派决定；平票或目录为空时回落到 hex——模板自身
# 的默认是 hex，时间戳前缀是派生项目可选加的约定（对应门禁的
# --require-revision-equals-timestamp-prefix）。
#
# 必须扫全目录，不能只看排序第一个文件：中途改过约定的仓库里，最老的那些迁移
# 会一直把它判回旧形态，而新旧文件从此各写各的。
#
# 比较前要归一化分隔符：文件名用 '-' 而 revision 用 '_' 是合法组合——模板自身的
# file_template 就是 '-'，而它早期版本的脚本写出的 revision 是 '_'。直接按字符串
# 比会把这类仓库误判成 hex 约定，凭空造出一个与全仓库格式不一致的 revision。
uses_timestamp_revision=0
migration_total_count=0
migration_timestamp_style_count=0
revision_separator=""
for existing_migration in "$versions_dir"/[0-9]*.py; do
    [ -e "$existing_migration" ] || continue
    migration_total_count=$(( migration_total_count + 1 ))
    existing_filename_prefix="$(basename "$existing_migration" | cut -c1-15)"
    existing_revision_value="$(read_revision_value "$existing_migration")"
    if [ -n "$existing_revision_value" ] &&
        [ "${existing_revision_value//-/_}" = "${existing_filename_prefix//-/_}" ]; then
        migration_timestamp_style_count=$(( migration_timestamp_style_count + 1 ))
        # 记录存量时间戳风格 revision 实际使用的分隔符（第 9 个字符）：比较时
        # 归一化是为了正确计数，写出时必须还原成项目自己的分隔符，否则文件名
        # 用 '-' 而 revision 用 '_' 的仓库会拿到一个与全仓库不一致的 revision。
        revision_separator="${existing_revision_value:8:1}"
    fi
done
if [ "$migration_timestamp_style_count" -gt 0 ] &&
    [ "$(( migration_timestamp_style_count * 2 ))" -gt "$migration_total_count" ]; then
    uses_timestamp_revision=1
fi

if [ -e "$target_path" ]; then
    echo "ERROR: target file already exists: $target_path" >&2
    exit 3
fi

# Find the new file that alembic just generated. We can't pre-name it because
# alembic revision does not expose --output-file; it always writes to the
# configured version_path with a name derived from -m + rev_id.
generated_files_before="$(ls "$versions_dir" 2>/dev/null || true)"

uv run alembic revision -m "$slug" >/dev/null

generated_files_after="$(ls "$versions_dir")"
generated_file="$(comm -13 \
    <(printf "%s\n" "$generated_files_before" | sed '/^$/d' | sort) \
    <(printf "%s\n" "$generated_files_after"  | sed '/^$/d' | sort) | head -n 1)"

if [ -z "$generated_file" ]; then
    echo "ERROR: could not locate the new migration file alembic just created." >&2
    exit 4
fi

generated_path="${versions_dir%/}/${generated_file}"
mv "$generated_path" "$target_path"

# Patch docstring header (Revision ID / Revises / Create Date) and, only when the
# project's convention calls for it, the `revision` variable.
create_date="$(date '+%Y-%m-%d %H:%M:%S.000000')"

# revision 用哪个值：项目若约定 revision == 时间戳前缀就改写成时间戳，否则保留
# alembic 生成的 hex。改写时用存量时间戳风格 revision 的分隔符（多数派探测时
# 记录），而不是文件名分隔符。Revision ID 文档串始终跟着实际的 revision 走——
# 两者不一致时，排查的人会照文档串去 `alembic downgrade`，然后发现那个 revision
# 根本不存在。
if [ "$uses_timestamp_revision" -eq 1 ]; then
    revision_value="${timestamp:0:8}${revision_separator:-$separator}${timestamp:9:6}"
else
    revision_value="$(read_revision_value "$target_path")"
fi

sed_in_place() {
    if sed --version >/dev/null 2>&1; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

sed_in_place \
    -e "s|^Revision ID: .*|Revision ID: ${revision_value}|" \
    -e "s|^Revises: .*|Revises: ${current_head}|" \
    -e "s|^Create Date: .*|Create Date: ${create_date}|" \
    -e "s|^revision: str = .*|revision: str = \"${revision_value}\"|" \
    "$target_path"

echo "Created: $target_path"
echo "down_revision: $current_head"
echo "Next: edit upgrade() and downgrade() in $target_path"
