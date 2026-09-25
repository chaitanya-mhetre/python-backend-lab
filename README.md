# Flowforge

A workflow-automation backend in Python: organizations, projects and tasks, plus
**"when X happens, do Y"** workflows that run in a background worker, with signed webhooks, RBAC,
audit logs, API keys and rate limiting.

**Stack:** FastAPI · Pydantic v2 · SQLAlchemy 2.0 (async) · PostgreSQL 16 · Alembic · Redis · arq ·
pytest · Docker · GitHub Actions

---

## Problem
Teams repeat the same manual steps: "when a task is done, create a follow-up, tell the owner,
ping our Slack bot". Flowforge stores those rules as workflow definitions (trigger → steps) and
runs them reliably in the background: retries, timeouts, no duplicate side effects after a
crash, and a full history of what ran.

## Why it exists
It's a learning-and-portfolio project that goes past CRUD. The interesting parts are the ones
backend interviews ask about: transactions, idempotency, concurrency control, pagination at
scale, authorization design, background processing, webhook security and observability.

## Architecture
```
client ─▶ FastAPI (request-id · rate limit · auth) ─▶ services (rules, @requires, audit) ─▶ repositories ─▶ PostgreSQL
                                             │ enqueue after commit
                                             ▼
                                  Redis (arq queue, cache, rate-limit buckets)
                                             │
                                             ▼
                          arq worker: dispatch_event → run_execution → deliver_webhook ─▶ receivers (HMAC-signed)
```
Details: [docs/architecture.md](docs/architecture.md) · schema: [docs/erd.md](docs/erd.md) ·
decisions: [docs/adr/](docs/adr/)

## Features
- **Auth:** register/login (argon2id, 15-min JWT) and **scoped API keys** (`ff_live_…`, hashed, revocable)
- **RBAC:** owner/admin/member/viewer as a permission table, enforced in services with `@requires`
- **Tasks:** state machine (todo → in_progress → done/cancelled), keyset pagination, filters,
  sorting, optimistic locking with `If-Match`/`ETag`
- **Workflows:** triggers `task.created` / `task.status_changed` with filters; steps
  `create_task`, `update_field`, `send_notification`, `call_webhook`, `delay`
- **Worker:** one transaction per step, retries with exponential backoff + jitter, step timeouts,
  cancellation, per-org concurrency limit, loop protection (depth ≤ 5), version pinning
- **Webhooks:** HMAC-SHA256 + timestamp signatures, encrypted secrets, SSRF guard, retry schedule
  1m/5m/30m/2h/12h, delivery log, redelivery
- **Audit log** for every change, written in the same transaction; **notifications** with unread state
- **Redis:** atomic Lua token-bucket rate limiter (429 + `Retry-After`), cache-aside project stats
- **Ops:** JSON logs with request id, Prometheus metrics (API `/metrics`, worker `:9101`),
  `/healthz` + `/readyz`, non-root Docker image, CI

## Tech stack
| | |
|---|---|
| API | FastAPI, Pydantic v2, uvicorn |
| Data | PostgreSQL 16, SQLAlchemy 2.0 async + asyncpg, Alembic |
| Background | arq on Redis 7 |
| Security | argon2-cffi, PyJWT, cryptography (Fernet), hmac |
| Observability | structlog, prometheus-client, prometheus-fastapi-instrumentator |
| Quality | pytest, pytest-asyncio, httpx, respx, ruff, mypy --strict |

## Quick start
```bash
uv sync
make up          # Postgres on :55432, Redis on :56379
make migrate
make run         # API on http://localhost:8000/docs
make worker      # in another terminal
./examples/api_walkthrough.sh   # needs jq
```
Or the whole stack in containers: `make up-all` (set `FLOWFORGE_API_PORT` if 8000 is taken).

## API usage
```bash
curl -X POST localhost:8000/api/v1/orgs/$ORG/workflows -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{
    "name": "follow up", "trigger_type": "task.status_changed", "trigger_filter": {"to": "done"},
    "steps": [{"type": "create_task", "config": {"title": "Retro: {title}"}}]}'
```
Full guide with error codes: [docs/api.md](docs/api.md).

## Testing
```bash
make check       # ruff + mypy --strict + all tests (needs `make up`)
make test-unit   # Docker-free subset
```
170 tests: domain, unit, contract (OpenAPI snapshot) and integration against real Postgres and
Redis, including crash-resume, concurrent-update and rate-limiter race tests.
Strategy: [docs/testing.md](docs/testing.md).

## Deployment
Local via Docker Compose (`make up-all`: migrate job → api + worker). The image is multi-stage
and runs as a non-root user. Cloud deployment is done in a separate project (`cloud-infra-lab`).

## Security
Summary in [docs/security.md](docs/security.md), including known limits (e.g. DNS rebinding for
webhooks).

## Performance
Measured, not estimated. Task list (first + second page, 50 rows) on 100,000 tasks, local Docker Postgres:

| | p50 | p95 |
|---|---|---|
| with `(project_id, created_at, id)` index | 1.78 ms | 2.41 ms |
| without | 26.92 ms | 34.81 ms |

Laptop numbers: the relative difference is the point. Method and environment:
[docs/benchmarks/task-list-20260925.md](docs/benchmarks/task-list-20260925.md) (`make bench`).
Cache hit ratio and worker throughput: **not measured yet**.

## Engineering trade-offs
- **Enqueue after commit** instead of an outbox: simpler, but an event can be lost in a crash
  window; pending rows + `scripts/requeue_pending.py` limit the damage ([ADR 0004](docs/adr/0004-enqueue-after-commit.md)).
- **Keyset pagination:** fast at any depth, but no random page access ([ADR 0003](docs/adr/0003-keyset-pagination.md)).
- **arq over Celery:** small and async-native, fewer built-in features ([ADR 0002](docs/adr/0002-arq-for-background-jobs.md)).
- **404 for non-members** instead of 403: hides which ids exist.
- **Invalidate, don't update** the cache: no stale writes from races; the TTL covers worker writes.

## Limitations
- `schedule.cron` trigger is modelled but not executed yet.
- No 2FA or account lockout.
- Webhook SSRF guard doesn't pin the resolved IP (DNS rebinding window).
- Worker-created tasks don't invalidate the stats cache (TTL, 60 s by default).
- Single-region, single-database design.

## Roadmap
- cron triggers via a scheduler job · webhook IP pinning 
· outbox pattern · OpenTelemetry traces

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under [MIT](LICENSE).
