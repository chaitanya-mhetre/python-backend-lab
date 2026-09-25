# ADR 0002: arq (Redis) for background jobs

**Status:** accepted (m4)

## Context
Workflow steps and webhook deliveries must run outside the request path. Options: Celery, RQ,
arq, or a hand-written Redis queue.

## Decision
arq: small, asyncio-native (our whole stack is async), and readable enough to understand fully.
Jobs are thin: they receive ids only and read the current state from Postgres.

## Consequences
- Redis is already a dependency (cache, rate limiter), so no new infrastructure.
- arq's feature set is smaller than Celery's (no canvas/chords, fewer integrations). Retries and
  scheduling are implemented in our own code (`worker/runner.py`) so the behaviour is explicit
  and tested; `production-fastapi` uses Celery for comparison.
- Redis is not a durable log: if it loses data, the database still knows what is unfinished
  (`scripts/requeue_pending.py`). See ADR 0004.
