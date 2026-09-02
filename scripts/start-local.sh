#!/usr/bin/env bash
# Local full-stack runner for macOS and Linux (no Docker required).
# Starts Redis (if installed/not running) + example_service + gateway.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PID_DIR="${ROOT_DIR}/.run"
LOG_DIR="${PID_DIR}/logs"
mkdir -p "${PID_DIR}" "${LOG_DIR}"

# Determine Python binary
if [[ -f "${ROOT_DIR}/.venv/bin/python" ]]; then
    PYTHON="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "Error: Python is not installed." >&2
    exit 1
fi

# Stop existing stack if running
if [[ -f "${SCRIPT_DIR}/stop-local.sh" ]]; then
    bash "${SCRIPT_DIR}/stop-local.sh" --quiet || true
fi

# Check / start Redis
is_port_open() {
    local port="$1"
    if command -v nc >/dev/null 2>&1; then
        nc -z 127.0.0.1 "$port" >/dev/null 2>&1
    else
        (echo > /dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1
    fi
}

if ! is_port_open 6379; then
    if command -v redis-server >/dev/null 2>&1; then
        echo "Starting redis-server..."
        redis-server --daemonize yes --port 6379 --save "" --appendonly no
    else
        echo "Warning: redis-server is not running on port 6379 and was not found in PATH."
        echo "Please start Redis manually or use 'docker compose up -d redis'."
    fi
else
    echo "Redis is already running on port 6379."
fi

# Environment variables
export JWT_SECRET="${JWT_SECRET:-change-me-in-production-use-a-long-random-string}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export RATE_LIMIT_REQUESTS="${RATE_LIMIT_REQUESTS:-10}"
export RATE_LIMIT_WINDOW_SECONDS="${RATE_LIMIT_WINDOW_SECONDS:-60}"
export RATE_LIMIT_FAIL_MODE="${RATE_LIMIT_FAIL_MODE:-closed}"
export EXAMPLE_UPSTREAM="${EXAMPLE_UPSTREAM:-http://127.0.0.1:8001}"

# Start example service
echo "Starting example service on port 8001..."
(
    cd "${ROOT_DIR}/services/example_service"
    "${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port 8001 > "${LOG_DIR}/example_service.out.log" 2> "${LOG_DIR}/example_service.err.log" &
    echo $! > "${PID_DIR}/example_service.pid"
)

# Start gateway
echo "Starting gateway on port 8000..."
(
    cd "${ROOT_DIR}/gateway"
    "${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "${LOG_DIR}/gateway.out.log" 2> "${LOG_DIR}/gateway.err.log" &
    echo $! > "${PID_DIR}/gateway.pid"
)

# Wait for HTTP readiness
wait_http() {
    local url="$1"
    local timeout=30
    local count=0
    while [ $count -lt $timeout ]; do
        if curl -s -f -o /dev/null "$url"; then
            return 0
        fi
        sleep 0.5
        count=$((count + 1))
    done
    return 1
}

echo "Waiting for services to become healthy..."
if wait_http "http://127.0.0.1:8001/health" && wait_http "http://127.0.0.1:8000/health"; then
    echo ""
    echo "========================================="
    echo "FULL STACK IS UP"
    echo "  Gateway:          http://127.0.0.1:8000/docs"
    echo "  Example Service:  http://127.0.0.1:8001"
    echo "  Redis:            127.0.0.1:6379"
    echo "========================================="
    echo "Stop with:  ./scripts/stop-local.sh"
    echo "Demo with:  ./scripts/demo.sh"
else
    echo "Error: Services failed to start. Logs:"
    cat "${LOG_DIR}/gateway.err.log" || true
    cat "${LOG_DIR}/example_service.err.log" || true
    exit 1
fi
