#!/bin/sh
# 以降权后的 runner 用户启动容器内私有 X11 显示，再运行原 daemon 命令。

set -eu

DISPLAY="${DISPLAY:-:99}"
export DISPLAY
SCREEN_SIZE="${KEDACODE_DESKTOP_SIZE:-1440x900x24}"
XVFB_LOG="/tmp/kedacode-xvfb.log"
OPENBOX_LOG="/tmp/kedacode-openbox.log"

for desktop_tool in Xvfb xdpyinfo openbox; do
    if ! command -v "${desktop_tool}" >/dev/null 2>&1; then
        echo "Hosted desktop is enabled but '${desktop_tool}' is unavailable; rebuild the desktop runner image." >&2
        exit 1
    fi
done

Xvfb "${DISPLAY}" -screen 0 "${SCREEN_SIZE}" -nolisten tcp -noreset >"${XVFB_LOG}" 2>&1 &
XVFB_PID=$!
attempt=0
while [ "${attempt}" -lt 40 ]; do
    if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
        echo "Xvfb exited before the desktop became ready:" >&2
        cat "${XVFB_LOG}" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 0.25
done

if ! xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
    echo "Xvfb did not become ready at ${DISPLAY}:" >&2
    cat "${XVFB_LOG}" >&2
    exit 1
fi

openbox --sm-disable >"${OPENBOX_LOG}" 2>&1 &
OPENBOX_PID=$!
sleep 0.25
if ! kill -0 "${OPENBOX_PID}" 2>/dev/null; then
    echo "Openbox exited while starting the desktop:" >&2
    cat "${OPENBOX_LOG}" >&2
    exit 1
fi

exec "$@"
