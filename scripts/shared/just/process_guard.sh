#!/usr/bin/env bash

# 进程数上限守护：兜住构建工具 fork 失控的场景。
#
# 背景：next dev(turbopack) 在陈旧 .next 产物下会无上限 spawn postcss worker，
# 每个 worker 加载 tailwindcss-oxide 并自带 rayon 线程池(50-78MB / ~9 线程)，
# 且任务完成后不回收。实测一个页面请求即 spawn 159 个，5 分钟堆到 3700 个进程
# / 123GB，把 32GB 机器压进 swap 假死。撞上 RLIMIT_NPROC 的代价只是该次构建
# 失败，远小于整机失去响应。
#
# 用法（在 justfile recipe 中，任何会起 dev server / 构建的入口）：
#     process_guard_script="{{justfile_directory()}}/scripts/shared/just/process_guard.sh"
#     if [ -f "$process_guard_script" ]; then
#         source "$process_guard_script"
#         apply_process_limit_guard
#     fi
#
# 关闭保护：RUN_PROCESS_HEADROOM=0 just run
# 调整余量：RUN_PROCESS_HEADROOM=800 just run

PROCESS_GUARD_DEFAULT_HEADROOM=400

# 给当前 shell 及其全部后代设置进程数上限。
#
# 关键语义：RLIMIT_NPROC 统计的是**当前 uid 的全部进程**，不是本 shell 的子进程，
# 因此上限必须按“当前进程数 + 余量”动态计算。写死一个小值（例如 400）在机器上
# 已有数百个进程时，会让调用方自身的命令都 fork 不出来。
apply_process_limit_guard() {
    local process_headroom="${RUN_PROCESS_HEADROOM:-$PROCESS_GUARD_DEFAULT_HEADROOM}"
    local current_process_count
    local process_limit_ceiling

    case "$process_headroom" in
        0)
            return 0
            ;;
        ''|*[!0-9]*)
            echo "⚠️  RUN_PROCESS_HEADROOM must be a non-negative integer; skipping process guard"
            return 0
            ;;
    esac

    current_process_count="$(pgrep -U "$(id -u)" 2>/dev/null | wc -l | tr -d ' ')"
    case "$current_process_count" in
        0|''|*[!0-9]*)
            echo "⚠️  Could not count current processes; skipping process guard"
            return 0
            ;;
    esac

    process_limit_ceiling=$((current_process_count + process_headroom))

    # ulimit 失败通常是 hard limit 低于目标值，此时放行而不是中断启动。
    if ulimit -u "$process_limit_ceiling" 2>/dev/null; then
        echo "Process guard: capped at $process_limit_ceiling processes (current $current_process_count + headroom $process_headroom)"
    else
        echo "⚠️  Could not set RLIMIT_NPROC to $process_limit_ceiling; continuing without process guard"
    fi
}
