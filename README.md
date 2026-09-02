# Distributed Rate Limiter & API Gateway

[![CI](https://github.com/your-username/rate_limiter/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/rate_limiter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-brightgreen.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-7.0+-DC382D.svg)](https://redis.io/)

A high-performance **FastAPI API Gateway** featuring:
- **Dual Authentication**: JWT Bearer tokens and `X-API-Key` headers.
- **Role-Based Access Control (RBAC)**: Fine-grained method and path authorization.
- **Sliding-Window Rate Limiter**: Atomic Redis Lua script execution over sorted sets (`ZSET`) per client identity.
- **Reverse Proxy Routing**: Clean URL prefix forwarding to upstream microservices with contextual identity headers (`X-Forwarded-*`).
- **Resilience Modes**: Configurable `fail-closed` (reject with 503) or `fail-open` (admit traffic) when Redis is unreachable.

---

##  Architecture

```
                      ┌──────────────────────────────────────────────┐
  Client              │               Gateway (:8000)                │
    │                 │                                              │
    │ HTTP            │  1. Auth & RBAC (JWT / API-Key check)        │
    ├────────────────►│  2. Sliding-Window Rate Limiter              │
    │                 │  3. Reverse Proxy Route Dispatcher (httpx)   │
    │                 └──────────────┬───────────────────────────────┘
    │                                │                      │
    │                                │ Proxy Request        │ Atomic Lua EVAL
    │                                ▼                      ▼
    │                     ┌────────────────────┐   ┌─────────────────┐
    │                     │  Example Service   │   │  Redis (:6379)  │
    │                     │      (:8001)       │   │  (ZSET window)  │
    │                     │  /items, /admin    │   └─────────────────┘
    │                     └────────────────────┘
```

### Inbound Middleware Order
FastAPI / Starlette executes middleware in reverse addition order (**last-added runs first**):
1. **Auth & RBAC Middleware** (Outer) — Unauthenticated (401) or unauthorized (403) requests fail immediately.
2. **Rate Limit Middleware** (Inner) — Only authenticated, authorized requests consume quota and interact with Redis.
3. **Route / Proxy Handler** — Dispatches local routes (`/auth/login`, `/health`, `/auth/me`) or proxies traffic to upstream services.

---

##  Quick Start

### Option 1: Docker Compose (Cross-Platform, Recommended)

Run the full stack with zero local dependencies beyond Docker:

```bash
# 1. Clone repository
git clone https://github.com/your-username/rate_limiter.git
cd rate_limiter

# 2. Start services (Redis + Example Upstream + Gateway)
docker compose up --build
```

- **Gateway Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Gateway Health**: [http://localhost:8000/health](http://localhost:8000/health)
- **Example Upstream Service**: [http://localhost:8001](http://localhost:8001)
- **Redis**: `localhost:6379`

---

### Option 2: macOS / Linux (Local Runner)

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Start Redis + Example Service + Gateway in background
./scripts/start-local.sh

# 3. Run automated end-to-end demo checks
./scripts/demo.sh

# 4. Stop local background services
./scripts/stop-local.sh
```

---

### Option 3: Windows (PowerShell)

```powershell
# 1. Setup portable Redis & dependencies (one-time setup)
.\scripts\bootstrap-redis.ps1
.\.venv\Scripts\python -m pip install -r requirements.txt

# 2. Start full stack
.\scripts\start-local.ps1

# 3. Run automated demo checks
.\scripts\demo.ps1

# 4. Stop stack
.\scripts\stop-local.ps1
```

---

##  Demo Accounts

> [!NOTE]
> **Demo Seed Accounts Only**: The credentials below are hardcoded in `gateway/app/auth/users.py` strictly for local testing, CI, and evaluation. In production, replace the in-memory store with an external identity provider (e.g., Auth0, Cognito, Keycloak, or Postgres/bcrypt).

| Username | Password    | Role    | API Key           | Permissions |
|----------|-------------|---------|-------------------|-------------|
| `alice`  | `alicepass` | `admin` | `alice-admin-key` | Full access (`GET`, `POST`, `PUT`, `DELETE` on `/items`, `/admin`) |
| `bob`    | `bobpass`   | `user`  | `bob-user-key`    | Read-only access (`GET`, `HEAD`, `OPTIONS` on `/items`); `/admin` and writes return `403` |

Default rate limit quota: **10 requests per 60 seconds** per identity.

---

##  Environment Variables

Copy `.env.example` to `.env` to customize settings:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET` | `change-me-in-production...` | Secret key used to sign and verify HMAC-SHA256 JWT tokens. **Generate your own for production using `openssl rand -hex 32`**. |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm. |
| `JWT_EXPIRE_MINUTES` | `60` | Token expiration time in minutes. |
| `REDIS_URL` | `redis://localhost:6379/0` | Connection string for Redis instance. |
| `RATE_LIMIT_REQUESTS` | `10` | Max number of allowed requests per window. |
| `RATE_LIMIT_WINDOW_SECONDS`| `60` | Duration of the sliding rate-limit window in seconds. |
| `RATE_LIMIT_FAIL_MODE` | `closed` | Fail mode when Redis is offline (`closed` returns `503 Service Unavailable`, `open` bypasses limiting). |
| `EXAMPLE_UPSTREAM` | `http://localhost:8001` | Base URL for the example microservice. |
| `LOG_LEVEL` | `INFO` | Gateway logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

---

##  Step-by-Step Guide: Adding Your Own Upstream Service

Adding a new microservice behind the gateway takes 4 simple steps:

### 1. Define the upstream configuration
Open [gateway/app/config.py](gateway/app/config.py) and add your service URL setting:

```python
class Settings(BaseSettings):
    # ... existing settings ...
    billing_upstream: str = "http://localhost:8004"
```

### 2. Register the route prefix in the Proxy Router
Open [gateway/app/proxy/router.py](gateway/app/proxy/router.py) and add the path prefix to `mounts`:

```python
mounts = (
    ("/items", settings.example_upstream),
    ("/admin", settings.example_upstream),
    ("/billing", settings.billing_upstream),  # <--- Add your service prefix here
)
```

### 3. Configure Role-Based Access Control (Optional)
Open [gateway/app/auth/rbac.py](gateway/app/auth/rbac.py) to protect your new prefix:

```python
PROTECTED_PREFIXES = ("/items", "/admin", "/billing")

def is_allowed(claims: UserClaims, method: str, path: str) -> bool:
    if claims.role == "admin":
        return True
    
    # Custom rule: regular users can only read invoices, not issue them
    if path.startswith("/billing/invoices"):
        return method.upper() in {"GET", "HEAD"}
        
    return False
```

### 4. Update Docker Compose (Optional)
Add your service container to [docker-compose.yml](docker-compose.yml):

```yaml
  service_billing:
    build:
      context: .
      dockerfile: services/billing/Dockerfile
    ports:
      - "8004:8004"
```
And pass `BILLING_UPSTREAM: http://service_billing:8004` to the `gateway` environment in `docker-compose.yml`.

---

## 📡 API Usage & Verification Examples

### 1. Health Check (Public)
```bash
curl -s http://localhost:8000/health
```
```json
{
  "status": "ok",
  "redis": "up",
  "rate_limit": {
    "requests": 10,
    "window_seconds": 60,
    "fail_mode": "closed"
  }
}
```

### 2. Obtain JWT Token via Login
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alicepass"}' \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

### 3. Call Protected Upstream Endpoint
```bash
curl -s http://localhost:8000/items -H "Authorization: Bearer $TOKEN"
```

### 4. Inspect Rate Limiting Headers
Every authenticated response includes standard rate limiting headers:
```bash
curl -i -s http://localhost:8000/items -H "X-API-Key: alice-admin-key"
```
```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9
X-RateLimit-Reset: 1788356369
```

When quota is exceeded:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 48
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1788356369

{"error":"rate_limited","detail":"Rate limit exceeded: 10 requests per 60 seconds","status_code":429}
```

---

##  Running Tests

Run the complete test suite locally using `pytest`:

```bash
pytest -v
```

Tests include:
- `tests/test_auth.py`: JWT generation, claims validation, invalid passwords, and API key authentication.
- `tests/test_rbac.py`: Authorization enforcement between `admin` and `user` roles across read and write operations.
- `tests/test_limiter.py`: Sliding window algorithm correctness, sliding timestamp eviction, Lua script execution, and fail modes.
- `tests/test_ratelimit_integration.py`: End-to-end rate limiting, response headers, and quota enforcement.

---

## 📄 License

Distributed under the [MIT License](LICENSE).
