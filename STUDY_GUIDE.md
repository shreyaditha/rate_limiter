# Interview study guide — Distributed Rate Limiter / API Gateway

This document describes **this repository**, not a generic gateway. File paths, function names, defaults, and failure behavior are taken from the code as it exists today.

If you can reconstruct the request path, the Lua script, middleware order, and the fail-closed default from memory, you can defend this project in an interview.

---

## 1. What it does and why it matters

### What it is

A **single HTTP entry point** (`gateway` on port 8000) that:

1. Authenticates the caller (JWT from `POST /auth/login`, or `X-API-Key`)
2. Authorizes the call (role vs path vs HTTP method)
3. Rate-limits **per identity** using Redis (sliding window, 10 req / 60 s by default)
4. Reverse-proxies the request to an upstream microservice by URL prefix

Mock backends are not the product. They exist so the gateway has something real to route to:

| Prefix         | Compose service     | Upstream (in Docker)              | App |
|----------------|---------------------|-----------------------------------|-----|
| `/items`, `/admin` | `example_service` | `http://example_service:8001` | `services/example_service/app/main.py` |

Redis holds **rate-limit state only** (sorted sets). It is not a session store, not a user DB, not a cache of API responses.

### The real-world problem

Companies do not want every microservice to re-implement auth, quotas, and routing. That duplicates bugs and makes policy inconsistent (service A allows 1000 req/min, service B allows 10). An **API gateway** is the choke point:

- **Protect backends** from abusive or buggy clients (DDoS-ish floods, retry storms, one tenant starving another).
- **Meter usage** for API products (“10 requests per minute per API key”).
- **Enforce who can call what** without putting JWT parsing in every service.
- **Give clients one URL** instead of discovering internal ports.

A recruiter-friendly one-liner:

> “I built a FastAPI gateway that authenticates with JWT, enforces RBAC, and rate-limits with an atomic Redis sliding window so multiple gateway workers cannot over-admit. An example microservice sits behind it so the proxy path is real, not a stub.”

### Why a company would build this (vs buy Kong/Envoy)

They might **buy** a gateway for TLS, WAF, canary, and ops. They still need engineers who understand **why** the limiter is atomic, **where** identity comes from, and **what happens when Redis dies**. This project is the teaching version of that control plane: small enough to read, specific enough to grill you on.

---

## 2. Full architecture walkthrough

### Process topology (`docker-compose.yml`)

```
Client
  │
  ▼
gateway:8000     FastAPI  (auth → RBAC → limiter → proxy or local route)
  │
  ├── redis:6379          Redis 7, no AOF/RDB persistence (ephemeral limiter state)
  └── example_service:8001
```

- Gateway `depends_on` Redis **healthy** (`redis-cli ping`) and `example_service` **started**.
- Redis command: `redis-server --save "" --appendonly no` — if Redis restarts, **all windows reset**. That is intentional for a demo limiter; production often still treats limiter state as disposable, but might use replication.
- Compose DNS: gateway talks to `redis://redis:6379/0` and `http://example_service:8001`, not `localhost`.

### Code layout (what lives where)

```
gateway/app/main.py                 create_app, lifespan, /health, /auth/login, /auth/me
gateway/app/config.py               Settings from env
gateway/app/errors.py               JSON envelope {"error","detail","status_code"}
gateway/app/auth/users.py           In-memory alice/bob seed accounts + API keys
gateway/app/auth/jwt.py             HS256 create/decode
gateway/app/auth/rbac.py            is_allowed(role, method, path)
gateway/app/middleware/auth.py      AuthMiddleware (auth + RBAC)
gateway/app/middleware/ratelimit.py RateLimitMiddleware
gateway/app/limiter/sliding_window.py  Lua + SlidingWindowRateLimiter.hit
gateway/app/proxy/router.py         httpx reverse proxy
services/example_service/app/main.py Upstream FastAPI microservice (:8001)
tests/fake_redis.py                 Python replica of the Lua semantics
```

### End-to-end request flow (authenticated `GET /items`)

Starlette runs middleware in **reverse registration order**. In `create_app` (`main.py`):

```python
app.add_middleware(RateLimitMiddleware, settings=settings)  # registered first → inner
app.add_middleware(AuthMiddleware, settings=settings)       # registered last  → runs first
```

**Inbound:**

1. **`AuthMiddleware.dispatch`** (`middleware/auth.py`)
   - Path `/items` is not public, method is not `OPTIONS`.
   - `_authenticate`: no `X-API-Key` → parse `Authorization: Bearer …` → `decode_access_token` in `auth/jwt.py`.
   - On success: `request.state.user = UserClaims(...)`, `request.state.api_key = request.headers.get("x-api-key")` (usually `None` for JWT-only).
   - `is_allowed(claims, "GET", "/items")` in `auth/rbac.py` → `True` for both `admin` and `user`.
   - `await call_next(request)` — does **not** touch Redis.

2. **`RateLimitMiddleware.dispatch`** (`middleware/ratelimit.py`)
   - Skips public paths; here it continues.
   - Reads `request.state.user`. If missing, it **does not** rate-limit (auth should already have 401’d).
   - `identity_key(user_id=..., api_key=...)` → `rl:user:usr_alice` (JWT) or `rl:apikey:{sha256[:24]}` (API key header present).
   - `limiter.hit(key)` → Redis `EVAL` of `SLIDING_WINDOW_LUA`.
   - If admitted: `call_next`, then attach `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
   - If rejected: **never calls the route**; returns 429 + `Retry-After`.

3. **Route match** — `build_proxy_router` registered paths `/items`, `/items/{path:path}`, `/admin`, `/admin/{path:path}`.
   - `_proxy` forwards request to `http://example_service:8001/items` (or `/admin/metrics`, etc.).
   - Strips hop-by-hop headers and **`Authorization`** so the JWT is not forwarded.
   - Adds `X-Forwarded-User`, `X-Forwarded-User-Id`, `X-Forwarded-Role`.
   - `httpx.AsyncClient.request` (timeout 10s, created in `lifespan`).
   - Returns upstream status/body, or **502** `bad_gateway` on `httpx.RequestError`.

**Local routes that never proxy:**

| Path | Auth | Rate limit | Handler |
|------|------|------------|---------|
| `POST /auth/login` | public | skipped | issues JWT |
| `GET /health` | public | skipped | Redis `PING` + config dump |
| `GET /docs`, `/redoc`, `/openapi.json` | public | skipped | FastAPI docs |
| `GET /auth/me` | required | **yes** | returns `request.state.user` |

### Why this structure

- **Gateway owns policy**; upstream services stay dumb JSON. That matches how real orgs split “edge” vs “domain.”
- **Redis beside the gateway**, not inside each service, so two gateway replicas share one counter (distributed limiter).
- **httpx client in lifespan**, not per request, so TCP connections to upstreams can be reused.

---

## 3. Design decisions and trade-offs

### 3.1 Sliding window vs fixed window vs token bucket

**This code:** Redis ZSET, score = unix **milliseconds**, member = `{now_ms}:{uuid}`. Window length = `RATE_LIMIT_WINDOW_SECONDS * 1000` (`Settings.rate_limit_window_ms`). Default **10 requests / 60 seconds**.

**Fixed window** (INCR + EXPIRE on `rl:user:alice:2026-08-24T19:51`): cheap, one integer. At the clock boundary a client can send `limit` at 00:00:59 and `limit` again at 00:01:00 → **2× burst**. Product specs usually mean “N in any rolling T seconds,” which this ZSET models.

**Token bucket / leaky bucket:** smoother bursts (refill rate). Harder to explain in an interview with Redis (`INCRBYFLOAT` + last timestamp, or Redis Cell). This repo chose the algorithm that maps 1:1 to “count timestamps in (now − T, now]” and is inspectable with `ZRANGE`.

**Trade-off accepted:** each identity stores **up to `limit` members** (not one counter). At 10 req/min that is nothing. At 1e6 req/min per key, ZSET memory and `ZREMRANGEBYSCORE` cost would push you toward a counter approximation (sliding log vs sliding counter).

### 3.2 Why Redis, not process memory, not Postgres

**In-memory dict on the gateway:** wrong as soon as you run **two uvicorn workers or two containers**. Each has its own counter → effective limit × N. The whole point of “distributed” in the project title is **shared state**.

**Postgres `INSERT` + `COUNT(*)` in a time range:** durable, transactional, slow for this hot path, and you still need a unique request id. Connection pool + WAL for every API hit is the wrong tool.

**Redis:** in-memory, microsecond ops, native sorted sets, **Lua EVAL is atomic on the server**. Compose uses a single Redis instance (not Cluster, not Sentinel). That is a **demo SPOF** — see section 6.

### 3.3 Why Lua EVAL, not `ZADD` then `ZCARD` in Python

`SlidingWindowRateLimiter.hit` issues **one** `redis.eval(script, 1, identity, now, window_ms, limit, request_id)`.

If two gateway tasks did:

```text
ZREMRANGEBYSCORE
ZCARD → 9
# both see 9, both ZADD → 11 admitted, limit was 10
```

Redis runs one Lua script to completion per shard before the next. Evict → count → maybe ZADD cannot interleave. That is the core showcase; do not describe it as “we call ZADD in Python.”

`FakeRedis.eval` in `tests/fake_redis.py` **reimplements the same control flow in Python** so pytest does not need Redis. Production still uses EVAL. If asked “do tests prove Lua?” — they prove the **algorithm**; they do not compile Lua. A follow-up would be an integration test against real Redis.

### 3.4 Why auth (and RBAC) before rate limiting

Requirement implemented in `main.py` docstring: **401/403 never increment the window.**

- Quota is **per identity** (`rl:user:…` / `rl:apikey:…`), not per IP.
- If you limited **before** auth, you would either key by IP (different product) or key by a token you have not validated (garbage tokens would need a bucket too).
- An attacker with no token **must not** burn Alice’s quota.
- **RBAC lives in the same middleware as auth**, so **403 also skips Redis**. Bob hammering `POST /items` does not consume his GET quota. That is a deliberate extra: the written requirement was only “after auth”; this implementation also skips unauthorized traffic.

Starlette gotcha to say out loud: **last `add_middleware` runs first.** If you reverse the two lines in `create_app`, you silently invert the pipeline.

### 3.5 Why JWT instead of server sessions

`create_access_token` puts `sub`, `username`, `role`, `iat`, `exp` in an **HS256** JWT (`auth/jwt.py`). Gateway verifies with `JWT_SECRET`. **No session table.**

- Gateway is stateless for auth (except the in-memory user store at **login** time). Horizontal scale of gateway workers does not need sticky sessions.
- Redis is kept for **rate limits**, not sessions — one tool, one job.
- Trade-off: **no revocation**. Stolen token works until `exp` (default 60 minutes, `JWT_EXPIRE_MINUTES`). Production would add `jti` + a denylist, or short-lived access + refresh, or introspection (OAuth).

Passwords in `users.py` are **plaintext**. Say that before they do: “demo seed accounts; production is bcrypt/Argon2 + external identity provider.”

### 3.6 Fail-closed vs fail-open (`RATE_LIMIT_FAIL_MODE`)

`Settings.fail_closed` is **True unless** the env var (stripped, lowercased) equals `"open"`. Default in Compose: **`closed`**.

On Redis error (`RedisError` or `OSError`, which includes `ConnectionError` from `FakeRedis`):

- **closed:** 503 `service_unavailable`, body `Rate limiter backend unavailable`. Upstream never sees the request. **Protects upstream services.**
- **open:** log warning, `call_next` **without** incrementing. Availability over enforcement.

If the limiter object itself is missing: same closed/open split (`Rate limiter unavailable`).

`GET /health` is always public: `redis` field `up`/`down`; `status` is `"ok"` if Redis pings **or** fail-open; `"degraded"` if fail-closed **and** ping fails. Health does **not** 503 just because Redis is down — load balancers can still mark the process alive while `/items` 503s.

**Why default closed:** this gateway’s job is protection. Fail-open turns a Redis outage into unbounded load on downstream backends. Fail-open is the right call for a paid API where **availability SLO** beats fair throttling, **and** you have other shedding.

### 3.7 Other choices in *this* code

**Identity: JWT user id vs `X-API-Key`.** `identity_key()` hashes the API key (`sha256` hex `[:24]`), namespaces `rl:apikey:` vs `rl:user:` so they cannot collide. Raw keys never stored in Redis.

**API key is also auth, not only a limiter label.** If `X-API-Key` is present, `_authenticate` **never looks at the Bearer token**. Unknown key → 401 even if JWT is valid. If both are sent and the key is **valid**, limiter keys on the **hash of the key**, not `user_id`. Documented in README.

**Login is unauthenticated and unmetered.** Credential stuffing against `POST /auth/login` is not limited. Real systems rate-limit login by IP **and** username.

**Proxy does not forward `Authorization`.** Backends trust `X-Forwarded-Role`. Compose **publishes 8001**, so a client can bypass the gateway. Production: private network only, or mTLS, or the backends re-validate.

**Hop-by-hop headers stripped** (`proxy/router.py` `HOP_BY_HOP`) so `Transfer-Encoding` etc. are not blindly copied (HTTP/1.1 proxy hygiene).

**httpx timeout 10 seconds.** Slow upstream holds a gateway worker that long. No retry, no circuit breaker.

**RBAC is prefix + method, not resource ACL.** `user` cannot touch `/admin*` at all; writes are `POST/PUT/PATCH/DELETE`. `GET/HEAD/OPTIONS` on `/items` allowed. Unknown roles → deny. `admin` → allow **everything** including `/auth/me`.

**OPTIONS is treated as public** in both middlewares (CORS preflight). There is **no CORSMiddleware** in the app — OPTIONS would still skip auth if a client sent it.

**Redis `PEXPIRE` on successful admit** sets TTL to **one window from now**, not from the oldest event. Idle keys disappear. Under continuous traffic, TTL keeps sliding forward; cardinality is still capped by `limit` after remrange.

**Clock source:** `now_ms` is **gateway `time.time()`**, passed into Lua as `ARGV[1]`. Redis `TIME` is not used. Multi-instance **clock skew** can slightly widen/narrow the window.

**`decode_responses=True`:** Lua return values may be int or str; `_to_int` handles bytes and ints.

**Error shape** is consistent for middleware and FastAPI `HTTPException` via `errors.py`. Proxy 502 uses `bad_gateway`. Validation → 422 `validation_error`.

**Tests inject `FakeRedis` + `FakeUpstream`** in `create_app(..., redis=, http_client=)` because httpx `ASGITransport` in this environment does not run FastAPI lifespan (`limiter` would be missing). That is why limiter is also attached **immediately** when `redis` is passed into `create_app`.

---

## 4. Core algorithms (detailed)

### 4.1 Sliding window on a Redis sorted set

**Data structure.** One ZSET per identity, e.g. `rl:user:usr_alice`.

| Redis concept | This project |
|---------------|----------------|
| **Score** | Event time in **milliseconds** (`now`) |
| **Member** | Unique string `{now}:{uuid4.hex}` so two hits in the same ms are two members |
| **Cardinality** | Number of requests still inside the window |

**Why a sorted set:** you need “delete everything with score ≤ cutoff” (`ZREMRANGEBYSCORE`) and “how many left?” (`ZCARD`) and “what is the oldest score?” (`ZRANGE 0 0 WITHSCORES`) for `X-RateLimit-Reset`.

**Window math.** Inclusive-old-side eviction:

```text
cutoff = now - window_ms
remove scores in [0, cutoff]   # Lua: ZREMRANGEBYSCORE key 0 cutoff
keep scores > cutoff           # i.e. (now - window, now]
```

Example: `window_ms = 1000`, hits at t=1000 and t=1100, limit=2.

- At t=1500: cutoff=500; both remain; third hit **denied**.
- At t=2001: cutoff=1001; score 1000 **evicted**; score 1100 remains; a new hit **allowed**.

This matches `test_window_slides_and_frees_quota` and `FakeRedis` (`s > cutoff`).

#### Lua, in order (`SLIDING_WINDOW_LUA`)

1. **`ZREMRANGEBYSCORE key 0 cutoff`**  
   Drop events that are older than or exactly `window_ms` old. Complexity O(log N + M) where M is removed.

2. **`ZCARD key`** → `current`  
   Count remaining events.

3. **If `current >= max_requests`:**  
   Do **not** `ZADD`.  
   `ZRANGE key 0 0 WITHSCORES` → oldest member.  
   `reset_at = oldest_score + window` (when that event will fall out).  
   If the set were empty (should not happen if current ≥ limit), fallback `now + window`.  
   Return `{0, current, 0, reset_at}` — allowed=0, remaining=0.

4. **Else admit:**  
   **`ZADD key now request_id`** — add this event. Same score, different members → both count.  
   **`PEXPIRE key window`** — key TTL in ms so abandoned identities do not leak memory.  
   `new_count = current + 1`, `remaining = max_requests - new_count`.  
   Oldest again for reset.  
   Return `{1, new_count, remaining, reset_at}`.

#### Python wrapper (`SlidingWindowRateLimiter.hit`)

- `now` default `int(time.time() * 1000)`; tests pass `now_ms=` to freeze time.
- `request_id = f"{now}:{uuid.uuid4().hex}"` — **same-millisecond collision on member name is avoided.** If member were only `str(now)`, the second ZADD in the same ms would **overwrite** the first (same member, new score) and **undercount**. This is the answer to “two requests at the same millisecond.”
- Maps Lua array → `RateLimitResult`.
- `reset_at` for the HTTP header: **unix seconds** (`reset_at_ms // 1000`).
- `retry_after`: seconds until reset, **ceil**, **minimum 1** so clients do not retry immediately:  
  `max(1, (reset_at_ms - now + 999) // 1000)`.

#### Headers (`RateLimitMiddleware`)

On allow **and** on 429:

- `X-RateLimit-Limit` = configured limit (not remaining+used from Redis independently)
- `X-RateLimit-Remaining` = after this hit (0 if denied)
- `X-RateLimit-Reset` = epoch seconds when the **oldest remaining** event ages out

429 also sets `Retry-After` to `retry_after`.

Public routes and 401/403 responses **do not** get these headers (rate-limit middleware never ran, or returned before `hit`).

### 4.2 JWT issuance and verification

**Login** (`POST /auth/login`, public):

1. `LoginRequest` validates non-empty username/password.
2. `authenticate` in `users.py`: dict lookup, **plaintext** password compare. Failure → FastAPI `HTTPException(401)` → envelope `unauthorized`.
3. `create_access_token`: payload `sub` (user_id), `username`, `role`, `iat`, `exp` (now + `jwt_expire_minutes`). Algorithm `HS256`, secret `JWT_SECRET`.
4. Response: `access_token`, `token_type=bearer`, `expires_in` in **seconds** (`minutes * 60`), `role`, `username`.

**Decode** (`decode_access_token`): `jwt.decode` with **explicit `algorithms=[HS256]`** (prevents alg-none attacks). Maps `sub` → `UserClaims.user_id`. PyJWT raises `ExpiredSignatureError` or `InvalidTokenError`.

**Auth middleware** catches both and returns **generic 401**. Clients **cannot** distinguish expired vs malformed vs missing. That is slightly worse UX, slightly better for attackers enumerating token quality.

**API key path:** `get_user_by_api_key` exact match on `alice-admin-key` / `bob-user-key`. Builds `UserClaims` including `api_key`. No expiry.

### 4.3 RBAC (`is_allowed`)

Evaluated **after** identity is known, **before** Redis.

```
admin → True (all methods, all paths that reached this function)
role not admin and not user → False
/auth/me → True for user
/admin* → False for user
/items* → True iff method not in {POST, PUT, PATCH, DELETE}
else False
```

`HEAD` and `OPTIONS` on `/items` are allowed for `user` (not in `_WRITE_METHODS`). Gateway OPTIONS short-circuit in middleware happens **before** RBAC for **all** paths including `/admin` — a `user` OPTIONS `/admin` would skip auth entirely. Niche CORS quirk.

---

## 5. Failure modes and edge cases

| Situation | What this code does |
|-----------|---------------------|
| **Redis down / timeout / `EVAL` throws** | Fail-closed: 503 JSON `service_unavailable`. Fail-open: request proceeds, **quota not incremented**. |
| **Limiter not on `app.state`** | Same closed/open as Redis; tests hit this when lifespan did not run, so `create_app` now attaches limiter when `redis=` is passed. `_resolve_limiter` walks `request.app.app…` because Starlette wrappers hide `state`. |
| **Token expired** | `ExpiredSignatureError` → 401, same body as missing token. **No** mid-handler re-check: verification is once at the start of the request. A token that expires **during** a 10s upstream call still completes if it was valid at dispatch. |
| **Two requests race at limit−1** | Lua atomicity: one `EVAL` sees `current=9`, ZADDs to 10; the other sees `current=10`, denies. **Correct.** |
| **Two requests, same millisecond** | Distinct members via UUID. Both counted. |
| **Same member if we had used only timestamp** | Second ZADD would replace the first; count would be wrong. We did not do that. |
| **Clock skew between gateway replicas** | `now` is local. A lagging clock keeps old scores “in window” longer (stricter). A fast clock evicts earlier (looser). Redis TIME would align them. |
| **Upstream down / DNS fail / connection reset** | `httpx.RequestError` → 502 `bad_gateway`. **Rate limit already incremented** (limiter ran before proxy). User spent quota on a failed call. Production often refunds or limits only on 2xx. |
| **Upstream slow** | Waits up to 10s; no 504 distinct from a hung client. |
| **Upstream 500** | Gateway forwards 500 body/status. Still counted. |
| **Wrong role** | 403, **no** Redis hit. |
| **No credentials** | 401, **no** Redis hit. |
| **Valid JWT + invalid `X-API-Key`** | 401 (key path wins and fails). |
| **Login brute force** | **Not limited.** |
| **Redis restart** | Empty ZSETs; everyone gets a fresh window. Compose disables persistence. |
| **Key TTL vs window** | `PEXPIRE` = window ms from last **admit**. A denied request does not refresh TTL. After idle, key vanishes even if you conceptually “owed” wait time — next admit starts a new window (correct for sliding window). |
| **Very large `limit`** | ZSET memory O(limit) per identity. |
| **Redis Cluster** | Script uses **one key** (`KEYS[1]`). Cluster-safe. Multiple keys in one Lua would need hash tags. |
| **`/health` when Redis down** | 200 with `redis: down` (fail-closed → `status: degraded`). Does not take the process out of rotation by itself. |
| **Direct call to :8001** | Bypasses auth, RBAC, and limiter. Ports are published in local/dev compose. |

---

## 6. How this would scale / what production still needs

What **already** scales horizontally for the limiter: many gateway processes, **one Redis**, same Lua, same key. That is the “distributed” part.

What a senior engineer would flag vs Kong / Envoy / AWS API Gateway:

1. **Identity store** — dict in `users.py`, plaintext passwords, static API keys. Need IdP, hashed secrets, key rotation, per-key limits (not one global 10/60).
2. **No TLS termination, WAF, request size limits, HTTP/2, gRPC.**
3. **No service discovery / retries / outlier ejection / circuit breaking** — one httpx client, one timeout, 502.
4. **Backends exposed** on host ports; trust `X-Forwarded-Role` without network policy.
5. **JWT HS256 shared secret** — RS256/ES256 + JWKS for multi-service verification; refresh tokens; revocation.
6. **Login unmetered**; no IP throttling; no lockout.
7. **Single Redis** — Sentinel/Cluster, timeouts, connection pool sizing, `SCRIPT LOAD` + `EVALSHA` instead of sending the full script every hit (this code sends the script body on **every** `eval`).
8. **No observability** — no metrics (admits vs 429s vs Redis errors), no tracing, unstructured logs.
9. **No per-route limits** — one global `RATE_LIMIT_REQUESTS` for all identities and all paths. Production: `alice` 1000/min on `/items`, 10/min on `/admin`.
10. **RBAC hardcoded** in `is_allowed`. Production: policy engine (OPA), method+path tables, tenants.
11. **Starlette `BaseHTTPMiddleware`** — extra task per request; high-QPS gateways use pure ASGI or Envoy filters.
12. **Quota spent before success** — 502 still counts. Decide product-wise.
13. **No idempotency keys**, no replay protection (`jti`).
14. **Config cache** — `get_settings` is `lru_cache`; process restart to change env (normal for containers).
15. **Docs/OpenAPI public** — often locked down.

Honest interview closer: “This is a correct **algorithm and middleware pipeline** on a teaching topology, not a replacement for Envoy.”

---

## 7. Interview questions (about *this* repo) with model answers

1. **What is the default limit and where is it set?**  
   10 requests per 60 seconds. `Settings.rate_limit_requests` / `rate_limit_window_seconds`; Compose `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`.

2. **What Redis type do you use and what is the key format?**  
   Sorted set. `rl:user:{user_id}` or `rl:apikey:{sha256(api_key)[:24]}` from `identity_key()`.

3. **Walk through the Lua script.**  
   Cutoff = now − window; `ZREMRANGEBYSCORE`; `ZCARD`; if ≥ limit, compute reset from oldest, return deny; else `ZADD` + `PEXPIRE`, return allow + remaining.

4. **Why UUID in the ZSET member?**  
   Members must be unique. Timestamp-only members would collapse same-ms hits into one.

5. **Is the limiter atomic?**  
   Yes, one `EVAL`. Not atomic if you split ZCARD/ZADD in Python. Tests use `FakeRedis.eval` as a single function matching that flow.

6. **Middleware order?**  
   `AuthMiddleware` then `RateLimitMiddleware` then route. Registered in reverse via `add_middleware`.

7. **Do 401s count against the quota?**  
   No. Unauthenticated requests never call `hit()`. Covered by `test_unauthenticated_does_not_consume_quota`.

8. **Do 403s count?**  
   No. RBAC is inside `AuthMiddleware` before `call_next`.

9. **How does Bob get 403 on `/admin/metrics`?**  
   `is_allowed`: role `user`, path startswith `/admin` → False. Covered by `test_user_cannot_access_admin_route`.

10. **Can Bob POST `/items`?**  
    No. `_WRITE_METHODS` rejects `POST` on `/items` for role `user`. Covered by `test_user_cannot_write_items`.

11. **What happens if Redis is down?**  
    Default fail-closed 503. `RATE_LIMIT_FAIL_MODE=open` skips limiter. Covered by `test_fail_closed_when_redis_down`.

12. **What JWT claims do you store?**  
    `sub`, `username`, `role`, `iat`, `exp`. Not the API key.

13. **How is the token verified?**  
    `jwt.decode` with secret + `algorithms=[HS256]`. Expired/invalid → 401.

14. **What is `/auth/me` for?**  
    Returns `request.state.user` without proxying. Still rate-limited.

15. **How does routing work?**  
    Prefix table in `build_proxy_router`; forwards to `EXAMPLE_UPSTREAM` (or custom upstream).

16. **Why strip `Authorization` toward upstream?**  
    Internal services should not see the user JWT; they get `X-Forwarded-*`.

17. **What does `X-RateLimit-Reset` mean here?**  
    Unix second when the **oldest event still in the ZSET** plus `window_ms` elapses — i.e. when a slot frees if you are at the cap.

18. **Is login rate-limited?**  
    No. Public path in `PUBLIC_PATHS`.

19. **How do tests run without Redis?**  
    `FakeRedis` implements `eval`/`ping`; `FakeUpstream` implements `request`; injected into `create_app`.

20. **What error JSON do clients always see from the gateway?**  
    `{"error": "...", "detail": "...", "status_code": N}` from `error_body` / `error_response`.

---

## 8. Gotcha follow-ups (prove you did not copy-paste)

**“Two requests at the exact same millisecond — do you double-count?”**  
Yes, we want that. Unique members `{ms}:{uuid}`. Same-ms is two requests.

**“Why not Postgres?”**  
Hot path, need atomic check-and-increment, no need for durability of each hit. Redis ZSET + Lua is the right primitive. DB would serialize on a row per user and still race without `SELECT FOR UPDATE`.

**“How do multiple gateway instances stay consistent?”**  
They do not store counts locally. Every `hit()` is Redis `EVAL` on the same key. Scale gateways; scale Redis separately. Two instances **cannot** over-admit because of Lua, **not** because of sticky sessions.

**“Could they over-admit anyway?”**  
Clock skew; fail-open; hitting backends directly on 8001; different identities (JWT vs API key → different Redis keys for the same human); login not limited.

**“If X-API-Key and Bearer are both sent?”**  
Key wins. Bad key → 401 ignoring JWT. Good key → quota on `rl:apikey:…`.

**“Does a 429 increment Redis?”**  
No. Deny path does not `ZADD`. Cardinality stays at `limit`.

**“Does a 502 increment?”**  
Yes. Limiter already admitted before `_proxy`.

**“Fixed window at minute boundary?”**  
We do not use calendar buckets. Sliding ZSET. Burst is at most `limit` in any interval of length `window`, not `2×limit` at a boundary.

**“Token bucket vs this?”**  
This is a **sliding log** (store each event). Token bucket stores tokens + timestamp. We chose log for spec fidelity and interview clarity, not max QPS.

**“What if Lua is copied to FakeRedis but Redis Lua has a bug?”**  
Unit tests would not catch it. Say you would run one test against Redis 7 in CI/`docker compose`.

**“PEXPIRE shorter than the window?”**  
If we expired the key too soon, we would forget recent hits and **under-count** (allow too many). We set PEXPIRE to `window` ms on each admit; remrange still enforces the sliding semantics.

**“Why `ZRANGE` oldest for Retry-After instead of `now + window`?”**  
When full, the next free slot is when the **oldest** event leaves, which can be **sooner** than a full window if some events are already aged. Example: limit 2, hits at t=0 and t=900, window 1000; at t=950 denied; reset ≈ 0+1000=1000, retry ~50ms, not 1000ms.

**“Is HS256 enough?”**  
For a single gateway with a server-side secret, yes. Multiple independently deployed services verifying tokens → asymmetric keys. Secret in Compose default is a **known string** — must change in real deploys.

**“Can I forge `X-Forwarded-Role: admin` on the gateway?”**  
Gateway **overwrites** those headers from JWT/API key after stripping hop-by-hop, but a client can still send them; they are set from `request.state.user` so the gateway’s values win **on the outbound httpx call**. Forging them **to the gateway** does not grant admin. Forging them **to port 8001** does, because example service ignores auth.

**“Starlette middleware order if I add a logger last?”**  
Last added = first on the way in. Always re-state that; people get it wrong.

**“Why `fail_closed` property `!= 'open'` rather than `== 'closed'`?”**  
Typos like `clsosed` still fail closed (safe default). Only explicit `open` fails open.

**“Does `/docs` consume quota?”**  
No. `PUBLIC_PATHS` + prefix `/docs`.

---

## Memory sheet (cheat side)

```
Pipeline:     Auth+RBAC → Redis sliding window → local handler or httpx proxy
Register:     add RateLimit first, Auth second  (Auth runs first)
Limit:        10 / 60s  (env)
Redis:        ZSET  score=ms  member=ms:uuid  EVAL atomic
Identity:     rl:user:{id}  or  rl:apikey:{sha256[:24]}
API key:      wins over JWT if header present
RBAC:         admin=all; user=GET /items; no /admin; no writes
Fail Redis:   default 503 (RATE_LIMIT_FAIL_MODE=closed)
Upstream down: 502, quota already used
401/403:      no Redis
429:          no ZADD, Retry-After + X-RateLimit-*
Login/health: public, no limiter
Users:        alice/alicepass admin; bob/bobpass user (demo seed accounts)
```

When they ask “tell me about a systems project,” walk this sheet top to bottom, then offer to draw the Lua steps on a whiteboard.
