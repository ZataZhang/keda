#!/usr/bin/env bash

# `just` 的 Bash 补全扩展，补齐 just 动态补全器不支持的 recipe 参数值。
#   - `just worktree`：`-o`、`-d`、`-D`、`-m` 和 `-r` 补全本地分支名。
#   - `just prd`：补全子命令与 scope。

_just_worktree_branch_candidates() {
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return 0
    fi

    git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null
}

_just_worktree_completion() {
    local cur recipe_name
    local branch_candidates option_candidates

    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    recipe_name="${COMP_WORDS[1]:-}"
    branch_candidates="$(_just_worktree_branch_candidates)"
    option_candidates="-o -d -D -m -r --doctor"

    case "$recipe_name" in
        worktree)
            if [[ "$COMP_CWORD" -eq 3 && "${COMP_WORDS[2]}" =~ ^-(o|d|D|m|r)$ ]]; then
                COMPREPLY=( $(compgen -W "$branch_candidates" -- "$cur") )
                return 0
            fi

            if [[ "$COMP_CWORD" -eq 2 && "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "$option_candidates" -- "$cur") )
                return 0
            fi
            ;;
        prd)
            if [[ "$COMP_CWORD" -eq 3 && "${COMP_WORDS[2]}" == "status" ]]; then
                COMPREPLY=( $(compgen -W "all pending archive" -- "$cur") )
                return 0
            fi

            if [[ "$COMP_CWORD" -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "status start heartbeat release" -- "$cur") )
                return 0
            fi
            ;;
    esac

    if declare -F _just >/dev/null 2>&1; then
        _just "$@"
        return $?
    fi

    return 0
}

if ! declare -F _just >/dev/null 2>&1; then
    eval "$(just --completions bash)"
fi

complete -F _just_worktree_completion -o bashdefault -o default just
