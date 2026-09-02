#!/usr/bin/env bash
# Stops background services started by start-local.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_DIR="${ROOT_DIR}/.run"
QUIET=false

for arg in "$@"; do
    if [[ "$arg" == "--quiet" || "$arg" == "-q" ]]; then
        QUIET=true
    fi
done

stop_pid() {
    local name="$1"
    local pid_file="${PID_DIR}/${name}.pid"
    if [[ -f "${pid_file}" ]]; then
        local pid
        pid=$(cat "${pid_file}" 2>/dev/null || true)
        if [[ -n "${pid}" ]]; then
            kill -9 "${pid}" 2>/dev/null || true
            if [ "$QUIET" = false ]; then
                echo "Stopped ${name} (pid ${pid})"
            fi
        fi
        rm -f "${pid_file}"
    fi
}

stop_pid "gateway"
stop_pid "example_service"

# Also free ports if bound
for port in 8000 8001; do
    if command -v lsof >/dev/null 2>&1; then
        pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
    fi
done

if [ "$QUIET" = false ]; then
    echo "Local stack stopped."
fi
