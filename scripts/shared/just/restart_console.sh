#!/usr/bin/env bash
# 重启本机正在跑的 Console，让新代码（尤其是后端路由）真正生效。
#
# 找到运行中的 kc console，按它当初被谁拉起的方式重启：
# 菜单栏 App 拉起的（父进程是 kedacode-desktop）就退出并重开 App，让 App 自己
# 重拉一份；否则原地重启同一端口，避免看板地址变化。
#
# Usage:
#   bash scripts/shared/just/restart_console.sh
set -euo pipefail

console_pid=""
for candidate in $(pgrep -f 'kc console' || true); do
    command_line="$(ps -o command= -p "$candidate" 2>/dev/null || true)"
    if [[ "$command_line" == *"kc console"* ]]; then
        console_pid="$candidate"
        break
    fi
done

if [ -z "$console_pid" ]; then
    echo "ℹ️  没有运行中的 kc console，跳过重启。"
    exit 0
fi

console_ppid="$(ps -o ppid= -p "$console_pid" | tr -d ' ')"
parent_command="$(ps -o command= -p "$console_ppid" 2>/dev/null || true)"

if [[ "$parent_command" == *"kedacode-desktop"* ]]; then
    echo "🔄 Console（pid $console_pid）由菜单栏 App 拉起，退出并重开 KedaCode.app…"
    osascript -e 'quit app "KedaCode"' >/dev/null 2>&1 || kill "$console_ppid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        pgrep -f 'kedacode-desktop' >/dev/null || break
        sleep 0.5
    done
    # open 紧跟 quit 时偶尔会因 LaunchServices 还没回收进程返回 -600，重试几次即可。
    launched=0
    for _ in $(seq 1 5); do
        if open -a "$HOME/Applications/KedaCode.app" >/dev/null 2>&1; then
            launched=1
            break
        fi
        sleep 1
    done
    if [ "$launched" -ne 1 ]; then
        echo "⚠️  未能重开 KedaCode.app，请手动从菜单栏启动。" >&2
        exit 1
    fi
    echo "✅ 已重开 KedaCode.app（Console 由它重新拉起，端口可能变化，以看板地址为准）。"
    exit 0
fi

port="$(ps -o command= -p "$console_pid" | sed -n 's/.*--port \([0-9]\{1,\}\).*/\1/p')"
if [ -z "$port" ]; then
    port=54545
fi
command_prefix="$(ps -o command= -p "$console_pid" | sed 's/ --port [0-9]*.*//')"
echo "🔄 重启独立 Console（pid $console_pid，端口 $port）…"
kill "$console_pid" 2>/dev/null || true
for _ in $(seq 1 20); do
    kill -0 "$console_pid" 2>/dev/null || break
    sleep 0.5
done
nohup $command_prefix --no-browser --port "$port" >/dev/null 2>&1 &
sleep 2
if curl -sf -o /dev/null "http://127.0.0.1:$port/api/v1/agent-runner/console/runtime/tasks"; then
    echo "✅ Console 已在端口 $port 重新拉起。"
else
    echo "⚠️  Console 启动后未就绪，请检查端口 $port 是否被占用。" >&2
    exit 1
fi
