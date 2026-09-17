#!/usr/bin/env bash

# 将此函数放入 .zshrc 或 .bashrc
# 用法:
#   source ./scripts/shared/worktree/open.sh && ai_open <worktree-name> [--cmd [code_cmd]]
#   或直接执行:
#   ./scripts/shared/worktree/open.sh <worktree-name> [--cmd [code_cmd]]
#
# <worktree-name> 接受看板与 PRD 流程里出现的各种名称：分支全名（feat/xxx）、
# 分支最后一段（xxx）、PRD slug、PRD 文件名（可带 .md，可带 tasks/... 路径）。

# 分支名 ↔ pending PRD 匹配规则的唯一事实源（与 create.sh 共用）。
_OPEN_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
source "$_OPEN_SCRIPT_DIR/prd_branch_match.sh"

ai_open_usage() {
    cat <<'EOF'
Usage:
  ai_open <worktree-name> [--cmd [code_cmd]]

Arguments:
  <worktree-name>   支持分支全名（feat/xxx）、分支最后一段（xxx）、PRD slug，
                    以及 PRD 文件名（P1-FEAT-20260916-212206-xxx，可带 .md，
                    也可写 tasks/pending/xxx.md 这样的路径）。
                    同名命中多个 worktree 时报错并列出候选，改用分支全名即可。

Options:
  --cmd [code_cmd]  使用指定命令打开 worktree 目录。
                    不传 code_cmd 时默认使用: code-insiders
  -h, --help        显示帮助

Examples:
  ai_open feature-login
  ai_open feature-login --cmd
  ai_open feature-login --cmd code
  ai_open P1-FEAT-20260916-212206-feature-login --cmd code
  ./scripts/shared/worktree/open.sh feature-login
  ./scripts/shared/worktree/open.sh feature-login --cmd code
EOF
}

# 把 <worktree-name> 归一化为候选名，按优先级每行输出一个：原样 → 最后一段 →
# 去 PRD 前缀与日期的 slug。PRD 文件名解析复用 prd_branch_match.sh，
# 禁止在调用方复制这套规则。
ai_open_collect_lookup_candidates() {
    local raw_input_text="$1"
    local trailing_stripped_text="${raw_input_text%/}"
    local basename_text="${trailing_stripped_text##*/}"
    basename_text="${basename_text%.md}"
    printf '%s\n' "$trailing_stripped_text"
    printf '%s\n' "$basename_text"
    printf '%s\n' "$(strip_prd_file_stem_to_slug "$basename_text")"
}

# 分支名是否命中候选集：分支全名或最后一段与任一候选相等即命中。
# 依赖全局 AI_OPEN_LOOKUP_CANDIDATES。
ai_open_branch_matches_candidate() {
    local branch_name_text="$1"
    local branch_basename_text="${branch_name_text##*/}"
    local candidate_text=""
    while IFS= read -r candidate_text; do
        [ -n "$candidate_text" ] || continue
        if [ "$branch_name_text" = "$candidate_text" ] ||
            [ "$branch_basename_text" = "$candidate_text" ]; then
            return 0
        fi
    done <<< "$AI_OPEN_LOOKUP_CANDIDATES"
    return 1
}

# 解析 <worktree-name> 对应的 worktree。
# 解析顺序：精确分支名 → slug 等价唯一命中 → 报错（歧义列候选 / 未命中列可用项）。
# 唯一事实源是 git worktree list，不猜路径：旧约定 $repo_parent/<branch> 既不是
# create.sh 的落盘位置，命中残留目录时还会打开 .git 已失效的工作目录。
# 结果经全局变量返回，调用方直接调用本函数，禁止放进 $( )：
#   AI_OPEN_RESOLVED_WORKTREE_PATH  worktree 绝对路径（未命中时为空）
#   AI_OPEN_RESOLVED_BRANCH         该 worktree 实际检出的分支名
#   AI_OPEN_RESOLVE_ERROR           未命中/歧义时的可读错误信息
ai_open_resolve_worktree_path() {
    local lookup_name_text="$1"
    local repo_root_path="$2"

    AI_OPEN_RESOLVED_WORKTREE_PATH=""
    AI_OPEN_RESOLVED_BRANCH=""
    AI_OPEN_RESOLVE_ERROR=""
    AI_OPEN_LOOKUP_CANDIDATES="$(ai_open_collect_lookup_candidates "$lookup_name_text")"

    local raw_porcelain_text=""
    raw_porcelain_text="$(git -C "$repo_root_path" worktree list --porcelain 2>/dev/null)"

    local raw_line_text=""
    local current_worktree_path=""
    local current_branch_name=""
    local exact_match_path=""
    local slug_match_count=0
    local slug_match_path=""
    local ambiguous_lines_text=""
    local available_lines_text=""

    while IFS= read -r raw_line_text; do
        case "$raw_line_text" in
            "worktree "*)
                current_worktree_path="${raw_line_text#worktree }"
                ;;
            "branch refs/heads/"*)
                current_branch_name="${raw_line_text#branch refs/heads/}"
                # 目录已消失的注册项（prune 之前）既不可打开也不该出现在候选里。
                [ -d "$current_worktree_path" ] || continue
                printf -v available_lines_text '%s    - %s  %s\n' \
                    "$available_lines_text" "$current_branch_name" "$current_worktree_path"
                if [ -z "$exact_match_path" ] &&
                    [ "$current_branch_name" = "$lookup_name_text" ]; then
                    exact_match_path="$current_worktree_path"
                fi
                if ai_open_branch_matches_candidate "$current_branch_name"; then
                    slug_match_count=$((slug_match_count + 1))
                    if [ "$slug_match_count" -eq 1 ]; then
                        AI_OPEN_RESOLVED_BRANCH="$current_branch_name"
                        slug_match_path="$current_worktree_path"
                    fi
                    printf -v ambiguous_lines_text '%s    - %s  %s\n' \
                        "$ambiguous_lines_text" "$current_branch_name" "$current_worktree_path"
                fi
                ;;
        esac
    done <<< "$raw_porcelain_text"

    # 1) 精确分支名优先：feat/x 与 tasks/x 并存时，显式输入的分支不能被判歧义。
    if [ -n "$exact_match_path" ]; then
        AI_OPEN_RESOLVED_WORKTREE_PATH="$exact_match_path"
        return 0
    fi

    # 2) slug 等价唯一命中：feat/<slug>、tasks/<slug>、PRD 文件名/路径都能打开。
    if [ "$slug_match_count" -eq 1 ]; then
        AI_OPEN_RESOLVED_WORKTREE_PATH="$slug_match_path"
        return 0
    fi

    # 3) 歧义：列候选、不猜，避免打开错误的 worktree。
    if [ "$slug_match_count" -gt 1 ]; then
        AI_OPEN_RESOLVE_ERROR="❌ '$lookup_name_text' 匹配到多个 worktree，请改用分支全名：
${ambiguous_lines_text}"
        return 1
    fi

    # 4) 未命中：列当前可打开的 worktree，替代只报"找不到"的无效提示。
    AI_OPEN_RESOLVE_ERROR="❌ 未找到与 '$lookup_name_text' 匹配的 worktree。
   可用名称：分支全名 / 分支最后一段 / PRD slug / PRD 文件名（可带 .md 与路径）。
   当前可打开的 worktree：
${available_lines_text:-    (无)}"
    return 1
}

function ai_open() {
    local worktree_name=""
    local vscode_command_name="code-insiders"
    local repo_root_path=""
    local worktree_path=""

    while [ "$#" -gt 0 ]; do
        case "$1" in
            -h|--help)
                ai_open_usage
                return 0
                ;;
            --cmd)
                if [ "$#" -gt 1 ] && [[ "$2" != -* ]]; then
                    vscode_command_name="$2"
                    shift
                fi
                ;;
            --cmd=*)
                vscode_command_name="${1#--cmd=}"
                if [ -z "$vscode_command_name" ]; then
                    echo "❌ --cmd= 后需要提供命令名，例如: --cmd=code"
                    return 1
                fi
                ;;
            -*)
                echo "❌ 未知参数: $1"
                ai_open_usage
                return 1
                ;;
            *)
                if [ -z "$worktree_name" ]; then
                    worktree_name="$1"
                else
                    echo "❌ 只允许一个名称参数，收到多余参数: $1"
                    ai_open_usage
                    return 1
                fi
                ;;
        esac
        shift
    done

    if [ -z "$worktree_name" ]; then
        echo "请提供 worktree 名称！例如: ai_open feature-login"
        ai_open_usage
        return 1
    fi

    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "❌ 当前目录不是 Git 仓库，无法定位 worktree。"
        return 1
    fi

    repo_root_path="$(git rev-parse --show-toplevel)"

    ai_open_resolve_worktree_path "$worktree_name" "$repo_root_path"
    worktree_path="$AI_OPEN_RESOLVED_WORKTREE_PATH"

    if [ -z "$worktree_path" ]; then
        printf '%s\n' "${AI_OPEN_RESOLVE_ERROR:-❌ 未找到与 '$worktree_name' 匹配的 worktree。}"
        return 1
    fi

    # 分支名匹配 pending PRD 时顺手领锁：无锁直接领取（归属记为该 worktree），
    # 同归属幂等刷新心跳；他人新鲜锁仅输出持锁者信息，不阻塞打开。
    # 领锁记解析出的真实分支名，按 PRD slug / 文件名打开时也能命中 pending PRD。
    local resolved_branch_name="${AI_OPEN_RESOLVED_BRANCH:-$worktree_name}"
    local prd_file_path=""
    if prd_file_path="$(find_pending_prd_for_branch "$repo_root_path" "$resolved_branch_name")"; then
        (cd "$worktree_path" && python3 "$repo_root_path/scripts/shared/just/prd_lock.py" \
            claim "$prd_file_path" --branch "$resolved_branch_name") || true
    fi

    if ! command -v "$vscode_command_name" >/dev/null 2>&1; then
        echo "❌ 未找到命令: $vscode_command_name"
        echo "   请确认该 CLI 已安装并在 PATH 中。"
        return 1
    fi

    echo "🚀 正在使用 $vscode_command_name 打开: $worktree_path ..."
    if ! "$vscode_command_name" "$worktree_path"; then
        echo "❌ 执行失败: $vscode_command_name \"$worktree_path\""
        return 1
    fi

    echo "✅ 已打开 worktree: $worktree_path"
}

# If executed directly with bash, run ai_open with all CLI args.
# If sourced in shell profile, only function definitions are loaded.
if [ -n "${BASH_VERSION:-}" ] && [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    ai_open "$@"
fi
