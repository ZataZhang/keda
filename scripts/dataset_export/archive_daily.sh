#!/usr/bin/env bash
# 定时归档：导出样本，写进数据集仓库的 raw/，有变化就提交并推送。
#
# 设计要点（原因写在这里，免得以后改坏）：
# - 用一个专用的只读工作树（RUNNER_DIR，固定在 keda 的 main）跑导出，而不是你正在改的检出：
#   你改到一半的代码不会让定时任务失败，定时任务也不会碰你的工作区。
# - 导出失败就不提交（set -e + 先导出后 git add），不会把残缺数据写进归档。
# - 只 git add raw/；curation/ 是人工补全，由人手工提交。
# - 运行锁防止上一次还没跑完又启动下一次；锁里存 PID，进程已不在就当陈旧锁清掉。
# - 整个脚本包在函数里、最后 exit：RUNNER_DIR 里的本脚本会在运行中被 checkout 更新，
#   bash 是边读边执行的，函数让它先整体解析，避免被更新到一半的文件弄坏。
#
# 默认只归档本人名下的仓库，不含公司仓库，也不含会话记录——那两项要先单独确认。
# 所有路径与仓库列表都可以用环境变量覆盖。

main() {
    set -euo pipefail

    local keda_dir="${KEDA_DIR:-$HOME/code/keda}"
    local dataset_dir="${DATASET_DIR:-$HOME/code/keda-datasets}"
    local runner_dir="${RUNNER_DIR:-$HOME/.kedacode/dataset-runner/keda}"
    local log_dir="${LOG_DIR:-$HOME/.kedacode/logs}"
    local keda_remote="${KEDA_REMOTE:-zata}"
    local commit_repos="${COMMIT_REPOS:-$HOME/code/keda $HOME/code/freshai $HOME/code/ZataTree}"
    local pr_repos="${PR_REPOS:-$HOME/code/keda $HOME/code/freshai}"
    local run_repo_ids="${KEDA_RUN_REPO_IDS:-keda keda-main freshai}"
    local lock_dir="${LOCK_DIR:-$HOME/.kedacode/dataset-archive.lock}"

    export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
    mkdir -p "$log_dir" "$(dirname "$lock_dir")"
    local stamp
    stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$stamp] dataset archive start"

    if ! mkdir "$lock_dir" 2>/dev/null; then
        local holder
        holder="$(cat "$lock_dir/pid" 2>/dev/null || true)"
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            echo "another archive run is in progress (pid $holder); skipping"
            return 0
        fi
        echo "removing stale lock (pid '${holder:-?}' is gone)"
        rm -rf "$lock_dir"
        mkdir "$lock_dir"
    fi
    echo "$$" >"$lock_dir/pid"
    trap 'rm -rf "'"$lock_dir"'"' EXIT

    # 1. 专用工作树固定在最新的 main
    git -C "$keda_dir" fetch --quiet "$keda_remote" || echo "warn: fetching $keda_remote failed; using the last fetched main"
    if [ ! -d "$runner_dir" ]; then
        mkdir -p "$(dirname "$runner_dir")"
        git -C "$keda_dir" worktree add --detach "$runner_dir" "$keda_remote/main" >/dev/null
    fi
    git -C "$runner_dir" checkout --quiet --detach "$keda_remote/main"

    # 2. 让各源仓库的远程引用是新的（只更新远程跟踪引用，不动工作区）
    local repo
    for repo in $commit_repos $pr_repos; do
        git -C "$repo" fetch --all --quiet 2>/dev/null || echo "warn: fetch failed in $repo"
    done
    git -C "$dataset_dir" pull --rebase --quiet origin main

    # 3. 导出并归档（失败则直接退出，不会提交）
    local -a args=(--with-gh --archive-dir "$dataset_dir")
    local id
    for id in $run_repo_ids; do args+=(--repo "$id"); done
    for repo in $commit_repos; do args+=(--commits-repo "$repo"); done
    for repo in $pr_repos; do args+=(--github-pr-repo "$repo"); done
    local err_file
    err_file="$(mktemp)"
    (cd "$runner_dir" && uv run python -m scripts.dataset_export "${args[@]}") 2>"$err_file" || {
        cat "$err_file"
        rm -f "$err_file"
        echo "export failed; nothing committed"
        return 1
    }
    cat "$err_file"

    # 4. 有变化才提交并推送（只提交 raw/）
    cd "$dataset_dir"
    git add -A raw MANIFEST.json
    if git diff --cached --quiet; then
        echo "no changes in the archive; nothing to commit"
        rm -f "$err_file"
        return 0
    fi
    local summary
    summary="$(grep '^归档:' "$err_file" | tail -1 || true)"
    git commit --quiet -m "data: archive export $(date '+%Y-%m-%d')" -m "${summary:-no summary}"
    git push --quiet origin main || {
        git pull --rebase --quiet origin main
        git push --quiet origin main
    }
    rm -f "$err_file"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] dataset archive done: ${summary:-ok}"
}

main "$@"
exit $?
