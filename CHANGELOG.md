# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Milestone tags: `m1` … `m7`.

## [Unreleased]
### Added
- Refresh tokens: `POST /auth/refresh` (rotation) and `POST /auth/logout`. Login now also returns
  `refresh_token` and `expires_in`. Reusing a spent refresh token revokes its whole family (#1).
- `schedule.cron` workflows now run: an arq cron job ticks every minute on every worker and a
  unique index on `(definition_id, scheduled_for)` guarantees one execution per scheduled time.
  Cron expressions are validated when saved (#2).

### Security
- Webhook delivery now connects to the IP address that passed the SSRF check instead of letting
  the HTTP client resolve the hostname again, closing the DNS-rebinding window (#3).

## [1.0.0] - 2026-09-25 (m7)
### Added
- Structured JSON logging (structlog) with request id and actor on every line.
- Prometheus metrics: HTTP (instrumentator) on `/metrics`, worker metrics on `:9101`.
- `/readyz` checks Postgres and Redis and returns 503 when either is down.
- Multi-stage, non-root Dockerfile; full `docker compose --profile app` stack (api, worker, migrate).
- GitHub Actions CI: ruff, mypy --strict, pytest against real Postgres/Redis, Docker build.
- OpenAPI snapshot contract test.
- `scripts/bench_tasks.py` (measured results in `docs/benchmarks/`), `scripts/requeue_pending.py`.
- Docs: architecture, ERD, API guide, ADRs, testing strategy, security notes, learning guide.

## [0.6.0] (m6)
### Added
- Outgoing webhooks: HMAC-SHA256 signatures with timestamp, Fernet-encrypted secrets, SSRF guard,
  retry schedule 1m/5m/30m/2h/12h, delivery log and redelivery.
- `call_webhook` workflow step. In-app notifications (assignment + workflow step) with read/unread.
- Request body size limit (413).

## [0.5.0] (m5)
### Added
- Redis token-bucket rate limiter (atomic Lua script) with `Retry-After`.
- Cache-aside project statistics with invalidation on task writes.
- Hashed, scoped API keys (`ff_live_…`), usable anywhere a JWT is, audited as `api_key` actors.

## [0.4.0] (m4)
### Added
- Workflow definitions API and background execution with arq: per-step transactions, retries with
  exponential backoff + jitter, step timeouts, delay steps, cancellation, per-org concurrency
  limits, loop protection, definition version pinning.

## [0.3.0] (m3)
### Added
- RBAC permission matrix and typed `@requires` decorator; member management.
- Audit log written in the same transaction as each change.
- Optimistic locking with `If-Match` / `ETag` (409 on conflict).

## [0.2.0] (m2)
### Added
- FastAPI app, async SQLAlchemy 2.0, Alembic migrations, JWT auth with argon2 hashing.
- Orgs, projects and tasks with keyset pagination, filtering and sorting.

## [0.1.0] (m1)
### Added
- Pure-Python domain: task state machine, generic repository protocol, `paginate` generator,
  synchronous workflow engine with loop detection.
