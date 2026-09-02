#!/usr/bin/env bash
# End-to-end demo against a running local or docker stack (curl-based).

set -euo pipefail

BASE_URL="http://127.0.0.1:8000"

echo "=== 1) Health Check ==="
HEALTH_RESP=$(curl -s "${BASE_URL}/health")
echo "Response: ${HEALTH_RESP}"
if [[ "${HEALTH_RESP}" != *"\"status\":\"ok\""* ]]; then
    echo "Health check failed!" >&2
    exit 1
fi

echo -e "\n=== 2) Login as alice (admin demo account) ==="
LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"alice","password":"alicepass"}')
echo "Response: ${LOGIN_RESP}"
TOKEN=$(echo "${LOGIN_RESP}" | python -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))" 2>/dev/null || \
        echo "${LOGIN_RESP}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [[ -z "${TOKEN}" ]]; then
    echo "Login failed to return access token!" >&2
    exit 1
fi

echo -e "\n=== 3) Authenticated GET /items ==="
ITEMS_RESP=$(curl -s -X GET "${BASE_URL}/items" -H "Authorization: Bearer ${TOKEN}")
echo "Response: ${ITEMS_RESP}"

echo -e "\n=== 4) RBAC: bob (user role) cannot POST /items or GET /admin/metrics (expect 403) ==="
BOB_LOGIN=$(curl -s -X POST "${BASE_URL}/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"bob","password":"bobpass"}')
BOB_TOKEN=$(echo "${BOB_LOGIN}" | python -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))" 2>/dev/null || \
            echo "${BOB_LOGIN}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

RBAC_POST_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE_URL}/items" \
    -H "Authorization: Bearer ${BOB_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"name":"Unauthorized","category":"test","price":1.0}')
echo "POST /items as bob returned status: ${RBAC_POST_CODE}"
if [[ "${RBAC_POST_CODE}" != "403" ]]; then
    echo "Expected 403 for bob POST /items but got ${RBAC_POST_CODE}" >&2
    exit 1
fi

RBAC_ADMIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X GET "${BASE_URL}/admin/metrics" \
    -H "Authorization: Bearer ${BOB_TOKEN}")
echo "GET /admin/metrics as bob returned status: ${RBAC_ADMIN_CODE}"
if [[ "${RBAC_ADMIN_CODE}" != "403" ]]; then
    echo "Expected 403 for bob GET /admin/metrics but got ${RBAC_ADMIN_CODE}" >&2
    exit 1
fi

echo -e "\n=== 5) Rate Limiting: 11 requests via X-API-Key (11th expect 429) ==="
LAST_CODE=""
for i in {1..11}; do
    RESP_HEADERS=$(curl -s -D - -o /dev/null -X GET "${BASE_URL}/items" -H "X-API-Key: alice-admin-key")
    STATUS=$(echo "${RESP_HEADERS}" | grep -E "HTTP/[12]" | tail -n1 | awk '{print $2}')
    REMAINING=$(echo "${RESP_HEADERS}" | grep -i "x-ratelimit-remaining:" | tr -d '\r' | awk '{print $2}' || echo "N/A")
    echo "Request $(printf "%2d" $i): Status=${STATUS} | Remaining=${REMAINING}"
    LAST_CODE="${STATUS}"
done

if [[ "${LAST_CODE}" != "429" ]]; then
    echo "Expected 11th request to be 429 but got ${LAST_CODE}" >&2
    exit 1
fi

echo -e "\n=============================="
echo "ALL DEMO CHECKS PASSED."
echo "=============================="
