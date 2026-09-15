#!/usr/bin/env bash

# prd_branch_match.sh — 分支名 ↔ pending PRD 匹配规则的唯一事实源。
# 被 create.sh（建 worktree 前领锁）、open.sh（打开 worktree 时顺手领锁）与
# justfile.shared 的 implement recipe（从 PRD 文件名推导分支名）共同 source，
# 禁止在调用方复制粘贴这套规则后微调。
#
# PRD 文件名规范（与 scripts/shared/just/prd_status.py 的 PRD_FILENAME_PATTERN
# 保持一致，任一端改动必须同步另一端）：
#   [P<n>-][KIND-]YYYYMMDD[-HHMMSS]-<slug>.md
# 分支匹配规则：slug 与分支全名相等，或与分支最后一段（basename）相等——
# 手动命名的 feat/<slug>、design/<slug> 与 implement 推导的 <slug> 都能命中。

# 从 PRD 文件名片段（去掉 .md）提取 slug；不符合规范命名时原样返回。
# 用法: slug="$(strip_prd_file_stem_to_slug "P2-FEAT-20260915-021337-my-feature")"
strip_prd_file_stem_to_slug() {
    local file_stem="$1"
    if [[ "$file_stem" =~ ^(P[0-9]+-)?([A-Z]+-)?[0-9]{8}-([0-9]{6}-)?(.+)$ ]]; then
        printf '%s\n' "${BASH_REMATCH[4]}"
    else
        printf '%s\n' "$file_stem"
    fi
}

# 在 tasks/pending 中查找与分支名匹配的 PRD 文件，命中时输出完整路径。
# 用法: prd_file="$(find_pending_prd_for_branch "$repo_root" "$branch")"
# 返回: 0 = 命中（stdout 为 PRD 文件路径）；1 = 无匹配或 pending 目录不存在。
find_pending_prd_for_branch() {
    local repo_root_path="$1"
    local branch_name="$2"
    local pending_dir_path="$repo_root_path/tasks/pending"

    [ -d "$pending_dir_path" ] || return 1

    local branch_basename="${branch_name##*/}"
    local prd_file_path=""
    local candidate_file_stem=""
    local candidate_slug=""
    for prd_file_path in "$pending_dir_path"/*.md; do
        [ -e "$prd_file_path" ] || continue
        candidate_file_stem="$(basename "$prd_file_path" .md)"
        candidate_slug="$(strip_prd_file_stem_to_slug "$candidate_file_stem")"
        if [ "$candidate_slug" = "$branch_name" ] || [ "$candidate_slug" = "$branch_basename" ]; then
            printf '%s\n' "$prd_file_path"
            return 0
        fi
    done
    return 1
}
