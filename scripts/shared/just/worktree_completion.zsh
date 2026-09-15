#compdef just

# `just` 的 Zsh 补全扩展，补齐 just 动态补全器不支持的 recipe 参数值。
#   - `just worktree`：`-o`、`-d`、`-D`、`-m` 和 `-r` 补全本地分支名。
#   - `just prd`：补全子命令与 scope。

# 在辅助函数中加载 just 的动态补全器，避免 autoload `_just` 时提前执行它。
_just_load_dynamic_completer() {
  source <(JUST_COMPLETE=zsh just)
}

_just_load_dynamic_completer
# just 的生成脚本会重绑命令；恢复到本扩展入口。
compdef _just just

_just_worktree_branch_candidates() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null
}

if (( CURRENT == 4 )) && [[ "${words[2]:-}" == worktree ]] &&
  [[ "${words[3]:-}" == -o || "${words[3]:-}" == -d || "${words[3]:-}" == -D || "${words[3]:-}" == -m || "${words[3]:-}" == -r ]]; then
  local -a branch_candidates
  branch_candidates=("${(@f)$(_just_worktree_branch_candidates)}")
  _describe 'local branch' branch_candidates
  return
fi

if (( CURRENT == 3 )) && [[ "${words[2]:-}" == worktree ]] && [[ "${PREFIX:-}" == -* ]]; then
  local -a option_candidates
  option_candidates=(
    '-o:open an existing worktree'
    '-d:delete a worktree and its local branch'
    '-D:force-delete a worktree and its local branch'
    '-m:merge a worktree'
    '-r:rebase-merge a worktree (linear history)'
    '--doctor:check and clean worktree state'
  )
  _describe 'worktree option' option_candidates
  return
fi

if (( CURRENT == 4 )) && [[ "${words[2]:-}" == prd ]] && [[ "${words[3]:-}" == status ]]; then
  local -a prd_scope_candidates
  prd_scope_candidates=(
    'all:展开 archive 每条'
    'pending:只看 pending'
    'archive:只看 archive（按月折叠）'
  )
  _describe 'prd scope' prd_scope_candidates
  return
fi

if (( CURRENT == 3 )) && [[ "${words[2]:-}" == prd ]]; then
  local -a prd_subcommand_candidates
  prd_subcommand_candidates=(
    'status:查看 PRD 状态看板'
    'start:领取 PRD 执行锁（--tool/--branch 自报）'
    'heartbeat:续期自己持有的执行锁'
    'release:释放执行锁（--force 强制）'
  )
  _describe 'prd subcommand' prd_subcommand_candidates
  return
fi

_clap_dynamic_completer_just "$@"
