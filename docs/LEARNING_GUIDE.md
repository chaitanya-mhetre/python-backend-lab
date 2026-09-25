# Flowforge learning guide

This guide is for studying the codebase until you can explain every part of it without notes.
Work through it in order. Each section says **what to read**, **what to notice** and **what to try**.
When you can answer the interview questions at the end out loud, you own this project.

How to use it:
1. `git checkout m1` and read only what existed then. Move through `m2` … `m7`. The history is
   the curriculum: each tag adds one layer.
2. For every file, first predict what it does from the name, then read, then run its tests.
3. Break things on purpose (delete a line, run `make check`) and see which test catches it.

---

## 0. The big picture (15 minutes)
Read `README.md`, then `docs/architecture.md`. Be able to draw this from memory:

```
HTTP → middleware → router → service → repository → Postgres
                                  └→ (after commit) Redis queue → worker → steps / webhooks
```

Three ideas carry the whole design:
- **Layers.** Routers do HTTP, services do rules and authorization, repositories do SQL. The domain
  package has no framework imports at all.
- **Postgres is the truth.** Redis (queue, cache, rate limits) can be wiped without losing
  committed data. Every job only carries ids and reads the real state from the database.
- **Every side effect is safe to retry.** Row locks, version columns and a "one success per step"
  unique index mean running something twice never does it twice.

---

## 1. M1: the pure-Python domain (`git checkout m1`)

**Read in this order:**
1. `src/flowforge/domain/task.py`
2. `src/flowforge/domain/errors.py`
3. `src/flowforge/domain/repository.py`
4. `src/flowforge/domain/pagination.py`
5. `src/flowforge/domain/workflow.py`
6. `src/flowforge/domain/engine.py`
7. `src/flowforge/domain/steps.py`
8. then `tests/domain/*`

| Concept | Where | What to notice |
|---|---|---|
| `StrEnum` | `TaskStatus` in `task.py` | members *are* strings, so they go straight into JSON and Postgres. `is_terminal` is a property on an enum. |
| Rules as data | `ALLOWED_TRANSITIONS` dict | the state machine is a lookup, not an if/else chain. The same idea returns in M3 (`PERMISSIONS`). |
| `@dataclass` + `field(default_factory=...)` | `Task.id` | `id: UUID = uuid4()` would run **once** at class creation and every task would share an id. `default_factory` runs per instance. |
| `__post_init__` validation | `Task.__post_init__` | normalises `title` (strip) and enforces the priority range. |
| Custom exceptions with data | `InvalidTransitionError(current, target)` | the base class `DomainError` has a `code`, which the API later maps to HTTP status codes in one place. |
| `Protocol` (structural typing) | `Repository[T]`, `HasId` | `InMemoryRepository` never inherits from `Repository`, yet mypy accepts it because it has the right methods. That's "duck typing, checked". Compare with an ABC, which *requires* inheritance. |
| Generics, PEP 695 syntax | `class InMemoryRepository[E: HasId]`, `def paginate[T](...)` | Python 3.12 type-parameter syntax, with a bound (`E` must have `.id`). |
| Generators | `paginate()`, `InMemoryRepository.find()` | `yield` produces pages lazily. `test_paginate_works_on_infinite_iterables` proves it never materialises everything. The walrus `while page := list(islice(...))` is idiomatic. |
| `frozen=True, slots=True` | `Event`, `Step` | immutable value objects; `slots` saves memory and blocks typos like `event.pyload = ...`. |
| `functools.cached_property` | `WorkflowDefinition.ordered_steps` | computed once per instance. |
| Callable protocol | `StepHandler` in `engine.py` | any function *or* object with `__call__(step, ctx)` works. `CreateTaskStep` is a class so it can hold a repository. |
| BFS with a queue | `WorkflowEngine.dispatch` | `collections.deque`: events are processed breadth-first. `Event.child()` increments `depth` → loop protection. |

**Try:** add a `BLOCKED` status that can go to/from `in_progress`. Which files change? (Only
`task.py`, plus the migration later.) Run the tests to see which ones need updating.

---

## 2. M2: HTTP, database, auth (`git checkout m2`)

**Read in this order:**
1. `config.py`
2. `db/base.py`
3. `db/models.py`
4. `db/session.py`
5. `security/passwords.py`
6. `security/tokens.py`
7. `repositories/pagination.py`
8. `repositories/tasks.py`
9. `services/tasks.py`
10. `api/deps.py`
11. `api/errors.py`
12. `api/routers/tasks.py`
13. `api/app.py`
14. `alembic/versions/0001_*.py`
15. `tests/integration/conftest.py`

| Concept | Where | What to notice |
|---|---|---|
| pydantic-settings | `config.py` | env vars with the `FLOWFORGE_` prefix; `SecretStr` hides secrets in reprs and logs; `@lru_cache` means the environment is parsed once. |
| SQLAlchemy 2.0 typed models | `db/models.py` | `Mapped[int]`, `mapped_column(...)`. Mixins (`UUIDPk`, `CreatedAt`) share columns. `pg_enum` stores enum *values*. |
| Naming conventions | `NAMING_CONVENTION` in `db/base.py` | stable constraint names make migrations reproducible. |
| Indexes, including partial ones | `TaskRow.__table_args__` | `(project_id, created_at, id)` serves keyset pagination; the partial `due_at WHERE status <> 'done'` index skips finished tasks. |
| Async sessions | `db/session.py` | `expire_on_commit=False` keeps objects readable after commit. `session_scope` is an `@asynccontextmanager`: commit on success, rollback on *any* exception, including `BaseException`. |
| Dependency injection | `api/deps.py` | `Depends` builds a graph per request: settings → session → principal → access. `Annotated[..., Depends(...)]` aliases (`SessionDep`) keep route signatures short. Tests swap nodes with `dependency_overrides`, or build the app with test objects (`create_app(queue=..., redis=...)`). |
| App factory | `create_app()` in `api/app.py` | no global app with global state, so each test builds its own. `lifespan` opens and closes the engine and Redis. |
| One error format | `api/errors.py` | `STATUS_BY_ERROR` maps domain error classes to HTTP codes. Order matters: subclasses come first. |
| argon2id | `security/passwords.py` | slow, memory-hard hashing. `AuthService.login` verifies against a dummy hash when the email doesn't exist, so response timing can't reveal which emails are registered. |
| JWT done safely | `security/tokens.py` | `algorithms=[...]` is pinned (never trust the token's `alg`); `exp` and `sub` are required. |
| Keyset pagination | `TaskRepository.list` | `tuple_(column, id) > tuple_(v, id)` is a SQL row comparison. It fetches `limit + 1` rows to detect a next page. The cursor is opaque base64 JSON. `test_rows_inserted_while_paging_do_not_cause_duplicates` shows why offset pagination is wrong. |
| PATCH semantics | `body.model_dump(exclude_unset=True)` in `routers/tasks.py` | only the fields the client sent are changed. |
| Domain rules reused | `TaskService.update` → `to_domain(row).transition_to(...)` | the M1 state machine guards the API, so the rule lives in one place. |
| Alembic | `alembic/env.py`, `versions/0001` | autogenerate, then **read and fix** the file: the `citext` extension and enum drops in `downgrade` were added by hand. |
| Real-DB tests | `tests/integration/conftest.py` | `migrated_db` runs `downgrade base` + `upgrade head` once per session (so migrations are tested too) and skips if Postgres is down. Tables are truncated after each test. `httpx.ASGITransport` calls the app in-process, with no network. |

**Try:** `EXPLAIN ANALYZE` the task-list query in `psql` (`docker exec -it flowforge-postgres-1 psql -U flowforge`)
with and without `ix_tasks_project_created`. Compare with `docs/benchmarks/`.

---

## 3. M3: authorization, audit, concurrency (`git checkout m3`)

**Read in this order:**
1. `security/permissions.py`
2. `services/access.py`
3. `services/orgs.py`
4. `repositories/audit.py`
5. `context.py`
6. `tests/unit/test_permissions.py`
7. `tests/integration/test_rbac_audit.py`

| Concept | Where | What to notice |
|---|---|---|
| RBAC as a table | `PERMISSIONS` | set algebra builds the roles (`_WRITE = _READ \| {...}`). The unit test writes the full expected matrix by hand. |
| Decorator factory + `ParamSpec` + `Concatenate` | `requires()` in `services/access.py` | `requires(action)` returns a decorator, which returns a wrapper. `Callable[Concatenate[S, OrgAccess, P], Awaitable[R]]` tells mypy "a method whose 2nd argument is `OrgAccess`, other arguments unchanged". `functools.wraps` keeps `__name__`/`__doc__`. The `/` makes `self, access` positional-only so the types line up. |
| 404 vs 403 | `AccessResolver.for_*` | strangers get 404 (they can't learn an id exists); members without the permission get 403. |
| `contextvars` | `context.py`, the middleware in `api/app.py`, `repositories/audit.py` | a per-request "global" that is safe under asyncio. The audit log reads the actor and request id without them being passed through every function. |
| Audit in the same transaction | `AuditRepository.record` only calls `session.add` | the row commits or rolls back **with** the change. `test_failed_change_leaves_no_audit_row` checks it. |
| Optimistic locking | `TaskRepository.update_if_version` | `UPDATE … WHERE id = :id AND version = :v RETURNING *`. Zero rows means someone else won. There's no `SELECT … FOR UPDATE` and no lock held while the user thinks. `If-Match`/`ETag` expose this over HTTP. |
| Why the race test works | `test_concurrent_updates_one_wins_one_conflicts` | 5 concurrent UPDATEs: Postgres row locks make them queue; after the first commits, the others re-check `version = 0`, find nothing, and return 409. |

**Try:** make admins unable to delete tasks. Change one line in `PERMISSIONS`, watch the matrix
test fail, and fix the expectation.

---

## 4. M4: background workflows (`git checkout m4`) (the hardest part)

**Read in this order:**
1. `jobs/queue.py`
2. `repositories/workflows.py`
3. `services/workflows.py`
4. `worker/steps.py`
5. `worker/runner.py` (read the module docstring twice)
6. `worker/settings.py`
7. `tests/integration/test_workflows.py`
8. `docs/adr/0004-enqueue-after-commit.md`

| Concept | Where | What to notice |
|---|---|---|
| Queue behind a Protocol | `JobQueue`, `ArqJobQueue`, `RecordingJobQueue` | production uses Redis; tests record jobs and run them with `drain()`, so background behaviour is deterministic. |
| Enqueue **after** commit | `TaskService._publish` | if we enqueued before commit and the commit failed, a worker would act on a change that never happened. After commit, the risk is losing the event in a crash window (ADR 0004). |
| Version pinning | `dispatch_event` stores `definition_version` + `steps_snapshot` | editing a workflow never changes runs already in flight (`test_editing_workflow_does_not_change_pinned_execution`). |
| One transaction per step | `_run_next_step` | the handler's writes (e.g. the new task) and its `step_runs` success row commit together. A crash rolls back both, and the step re-runs cleanly. |
| Row lock | `executions.get(..., for_update=True)` | two workers can never run the same execution concurrently; the second waits, then sees the step done. |
| SAVEPOINT | `session.begin_nested()` in `_call_handler` | a failing handler rolls back only its own writes; the outer transaction still records the failed attempt. |
| DB-enforced idempotency | partial unique index `uq_step_runs_one_success` | even a bug can't record two successes for the same step. |
| Timeouts | `asyncio.timeout(deps.step_timeout)` | cancels the handler's coroutine; `TimeoutError` becomes a normal failure. |
| Retries with backoff + jitter | `backoff_delay`, `_record_failure` | the retry is a *new deferred job*, not a sleep in the worker, so it survives restarts and doesn't block a worker slot. "Full jitter" spreads out thundering herds. |
| Delay steps | `delay` handler returns `defer_seconds` | the runner re-enqueues the execution with a defer instead of sleeping. |
| Per-org fairness | `WorkerDeps.org_limit` → `asyncio.Semaphore` | one noisy org can't take all worker slots (`test_per_org_concurrency_limit`). |
| `asyncio.TaskGroup` | `publish_events` | structured concurrency: if one enqueue fails, the others are cancelled and the error propagates. |
| Crash testing | `SimulatedCrash(BaseException)` | `except Exception` doesn't catch it, just like a real `SystemExit`. After the "crash", re-running produces exactly one task from step 1. |
| Loop protection | `event.depth > max_depth` in `dispatch_event` | an execution is created as FAILED with `workflow_loop_detected`, so the loop is visible in the history. |

**Try:** in `_run_next_step`, move `session.add(StepRunRow(... SUCCEEDED ...))` into a separate
`session_scope` (a separate transaction). Which test breaks, and why? (Crash between the two
commits → duplicate task on retry.)

---

## 5. M5: Redis (`git checkout m5`)

**Read in this order:**
1. `security/rate_limit.py`
2. `api/rate_limit.py`
3. `services/cache.py`
4. `services/projects.py` (`stats`)
5. `security/api_keys.py`
6. `services/api_keys.py`
7. `security/principal.py`
8. `api/deps.py` (`get_principal`)

| Concept | Where | What to notice |
|---|---|---|
| Token bucket | `TOKEN_BUCKET_LUA` | refill = elapsed × rate, capped at capacity. The Lua script makes "read → decide → write" atomic. It uses `redis.call('TIME')` so all API servers share one clock. |
| Why atomicity matters | `test_rate_limit_is_exact_under_concurrency` | 20 concurrent requests, bucket of 5 → exactly 5 pass. With GET-then-SET in Python, more would slip through. |
| Middleware order | `install_rate_limit` added last in `create_app` | Starlette runs the last-added middleware first, so the limiter runs before auth and throttles login brute force too. |
| Cache-aside | `ProjectService.stats` | read Redis → miss → compute from Postgres → store with TTL. Authorization **still runs on a hit** (`test_cache_hit_still_checks_authorization`). |
| Invalidate, don't update | `TaskService` calls `cache.invalidate` after commit | deleting the key can't leave a stale value behind in a race; the next reader recomputes. |
| API keys | `security/api_keys.py` | a random 256-bit secret means a fast SHA-256 is fine (brute force is hopeless), unlike passwords. The prefix is used for lookup, `hmac.compare_digest` for comparison, and the key is shown once. |
| Principal abstraction | `Principal(user=...)` / `Principal(api_key=...)` | routes don't care which kind of caller it is; `AccessResolver.for_org` gives keys `scopes` instead of a role. |
| Least privilege | `ApiKeyService.create` | scopes must be a subset of the creator's permissions (no escalation). Service-internal lookups use `load_in_org` so a `task:create` key doesn't also need `project:read`. |

---

## 6. M6: webhooks and notifications (`git checkout m6`)

**Read in this order:**
1. `security/webhooks.py`
2. `worker/deliveries.py`
3. `worker/webhooks.py`
4. `services/webhooks.py`
5. `examples/verify_webhook.py`
6. `tests/unit/test_webhook_security.py`
7. `tests/integration/test_webhooks_notifications.py`

| Concept | Where | What to notice |
|---|---|---|
| HMAC signatures | `sign()` | `HMAC-SHA256(secret, "timestamp.body")`. The timestamp is inside the signed string, so receivers can reject old copies (replay protection). |
| Constant-time compare | `hmac.compare_digest` | a plain `==` returns early at the first wrong byte, which leaks timing information. |
| Encryption vs hashing | `SecretBox` (Fernet) | we must *read* the webhook secret back to sign with it, so it's encrypted, not hashed. Passwords are hashed because we never need them back. |
| SSRF | `check_url` | resolve the host, reject private, loopback, link-local (cloud metadata at `169.254.169.254`) and reserved addresses. The check runs at save **and** send time. The known gap (DNS rebinding) is written up in `docs/security.md`. |
| Injectable dependencies | `resolver`, `clock`, `http_client` on `WorkerDeps` | this is what makes DNS, time and HTTP testable (fake resolver, frozen clock, respx). |
| Mini-outbox | `create_deliveries` writes rows in the same transaction as their cause | the queue is just a trigger; `scripts/requeue_pending.py` can rebuild it from the database. |
| Fixed retry schedule | `RETRY_SCHEDULE` | 1m, 5m, 30m, 2h, 12h: fast recovery for blips, patience for outages. The test asserts the exact `next_retry_at` with a frozen clock. |
| At-least-once delivery | `redeliver` reuses the payload `id` | receivers must deduplicate on it; exactly-once over HTTP isn't possible. |

---

## 7. M7: operations (`git checkout m7`)

**Read in this order:**
1. `observability/logging.py`
2. `observability/metrics.py`
3. `api/routers/health.py`
4. `Dockerfile`
5. `docker-compose.yml`
6. `.github/workflows/ci.yml`
7. `tests/contract/test_openapi_snapshot.py`
8. `scripts/bench_tasks.py`

| Concept | Where | What to notice |
|---|---|---|
| Structured logs | `configure_logging` | JSON lines with `request_id` and `actor` added automatically from contextvars. stdlib loggers go through the same pipeline (`foreign_pre_chain`). |
| Liveness vs readiness | `/healthz` vs `/readyz` | liveness = "restart me if this fails" (never check dependencies). Readiness = "don't send me traffic" (checks DB + Redis, returns 503). |
| Metrics | `WORKFLOW_EXECUTIONS`, `STEP_DURATION` | counters vs histograms. The worker exposes its own `/metrics` on `:9101` because it's a separate process. |
| Multi-stage Docker | `Dockerfile` | dependencies go in their own cached layer (`--no-install-project`); the runtime image has no uv or compilers, runs as a non-root user and has a healthcheck. |
| Compose profiles | `profiles: ["app"]` | `make up` starts only infra for local development; `make up-all` adds a one-shot `migrate` job that api and worker wait on (`service_completed_successfully`). |
| CI with real services | `ci.yml` `services:` | Postgres and Redis containers, the same commands as `make check`, then a Docker build. |
| Contract test | OpenAPI snapshot | an accidental API change fails CI; an intended one is re-snapshotted in the same PR. |
| Honest benchmarks | `bench_tasks.py` | `DROP INDEX` inside a transaction then `ROLLBACK`: Postgres DDL is transactional, so this is a safe A/B test. The report records the machine and command. |

---

## 8. Interview questions (answer out loud, then check)

1. **Walk me through what happens when a task's status changes.**
   PATCH → auth → `AccessResolver` → `@requires(TASK_UPDATE)` → version check → domain
   `transition_to` → conditional `UPDATE … WHERE version` → audit row → commit → invalidate cache →
   enqueue `dispatch_event`. The worker matches workflows, creates pinned executions and webhook
   deliveries, and runs the steps one transaction each.

2. **How do you prevent lost updates?**
   Optimistic locking: a `version` column, `UPDATE … WHERE version = :expected`, 0 rows → 409. The
   client sends `If-Match`. No locks are held between read and write.

3. **Optimistic vs pessimistic locking: when would you use each?**
   Optimistic when conflicts are rare and the user thinks between read and write (edit forms).
   Pessimistic (`SELECT … FOR UPDATE`) for short, high-contention critical sections. The worker
   uses it on the execution row, because two workers must never run the same execution.

4. **Why cursor pagination instead of offset?**
   `OFFSET n` scans and discards n rows (slow when deep) and shifts when rows are inserted
   (duplicates or gaps). Keyset uses an index range scan from the last key and is stable. The cost:
   no jumping to page N.

5. **What happens if the worker crashes mid-step?**
   That step's transaction (side effects + step_run) rolls back. Committed steps are skipped on
   re-run. The row lock blocks concurrent re-runs, and the partial unique index guarantees at most
   one success per step.

6. **How do you make a job idempotent?**
   Jobs carry ids, not data. They re-read state, skip if already done, and record completion in
   the same transaction as the effect. Unique constraints are the last line of defence.

7. **Why enqueue after commit? What's the risk and the proper fix?**
   Before commit risks phantom work if the commit fails. After commit risks losing the event in
   the gap. The fix is a transactional outbox (event row in the same transaction + a relay). The
   interim safety net is pending rows plus the requeue script.

8. **How does your rate limiter stay correct under concurrency?**
   The token-bucket state lives in a Redis hash and is updated by a Lua script, which runs
   atomically. It uses Redis `TIME` for a single clock. The test fires 20 requests at a bucket of
   5 and exactly 5 pass.

9. **Token bucket vs fixed window vs sliding window?**
   Fixed windows allow 2× bursts at the window edges. Sliding windows are precise but cost more
   memory. A token bucket allows controlled bursts (capacity) at a sustained rate (refill), with
   O(1) state.

10. **Cache invalidation strategy? What can go stale?**
    Cache-aside with delete-on-write, plus a TTL. Worker-created tasks don't invalidate, so stats
    can be up to 60 s stale; the TTL bounds it. Authorization runs even on cache hits.

11. **Protocol vs ABC?**
    Protocol = structural ("has these methods"); no inheritance needed, which suits fakes and
    third-party classes. ABC = nominal; subclasses must inherit, and it can provide shared
    implementation and enforce abstract methods at instantiation.

12. **When do you use a generator?**
    For lazy or streaming sequences: large or infinite data, pipelines, pagination. Memory stays
    O(page) instead of O(all). See `paginate`.

13. **Explain your `@requires` decorator's typing.**
    It's a decorator factory. `ParamSpec P` captures the remaining parameters and `Concatenate`
    pins `(self, OrgAccess)` in front, so the decorated method keeps its exact signature for
    type-checkers.

14. **What are contextvars and why not thread-locals?**
    asyncio runs many requests on one thread. A thread-local would be shared between them. A
    ContextVar is per task: each request sees its own request id and actor.

15. **How do you store passwords vs API keys vs webhook secrets?**
    Passwords: argon2id (slow and salted, because they have low entropy). API keys: SHA-256 (high
    entropy, fast lookups, never needed back). Webhook secrets: encrypted with Fernet, because we
    need the plaintext to sign.

16. **How do receivers trust your webhooks?**
    HMAC-SHA256 over `timestamp.body` with a per-webhook secret. They verify with a constant-time
    compare, reject messages older than 5 minutes, and deduplicate on the event id.

17. **What is SSRF and how do you prevent it here?**
    The server is tricked into requesting internal URLs (e.g. cloud metadata). We allow https only,
    resolve the host and refuse non-public IPs, at creation and before every send. The remaining
    risk is DNS rebinding; the fix is pinning the checked IP or using an egress proxy.

18. **Why does a non-member get 404 instead of 403?**
    403 confirms the resource exists. 404 reveals nothing, which prevents id enumeration across
    tenants.

19. **How are workflow loops prevented?**
    Events carry `depth`; each workflow-caused event is `depth + 1`; beyond 5 the execution fails
    visibly with `workflow_loop_detected`. Webhook subscribers only receive first-hand events.

20. **Liveness vs readiness probes?**
    Liveness: is the process healthy (restart it if not); never check dependencies, or a DB outage
    restarts every pod. Readiness: can it serve right now; checks dependencies and takes the pod
    out of the load balancer.

21. **How do you test background jobs deterministically?**
    Inject the queue. Tests record jobs and `drain()` them, and inject the clock, resolver and HTTP
    client. One test runs a real arq worker to prove the wiring.

22. **What does the SAVEPOINT in the worker buy you?**
    A failing handler's partial writes are rolled back without aborting the outer transaction, so
    we can still record the failed attempt and schedule the retry atomically.

23. **How did you prove the composite index matters?**
    `scripts/bench_tasks.py`: 100k rows, p50 of about 1.8 ms with the index vs about 27 ms without,
    measured on a laptop with the method documented. The index was dropped inside a rolled-back
    transaction.

24. **What would you change for production scale?**
    An outbox instead of enqueue-after-commit, refresh tokens and key expiry, OpenTelemetry
    tracing, read replicas for heavy lists, partitioning `audit_logs` by time, webhook IP pinning
    or an egress proxy, and a cron scheduler.

---

## 9. Exercises to prove you understand it
- Add a per-workflow time zone to cron schedules (`trigger_filter.tz`), including DST tests.
- Add `expires_at` to API keys (model, migration, auth check, test).
- Make worker-created tasks invalidate the stats cache (give `WorkerDeps` the cache).
- Replace enqueue-after-commit with an outbox table + relay job; prove no event loss with a test
  that crashes between commit and enqueue.

---

## 10. After v1: improvements (one issue each)

### 10.1 Refresh tokens with rotation and reuse detection (#1)
Read in this order: `security/tokens.py` (`new_refresh_token`) → `db/models.py` (`RefreshToken`)
→ `services/auth.py` (`refresh`, `logout`) → `tests/integration/test_refresh_tokens.py`.

- **Two token types, two jobs.** The access token is a stateless JWT: fast to check, but it can't
  be revoked, so it lives only 15 minutes. The refresh token is an opaque random string checked
  against the database, so it *can* be revoked, and lives 14 days.
- **Stored as a hash.** Like API keys, only `sha256(token)` is stored. SHA-256 (not argon2) is fine
  because the token has 256 bits of randomness; slow hashing only matters for guessable inputs.
- **Rotation.** Every refresh marks the presented row `used_at` and issues a child row with the same
  `family_id`. A token works exactly once.
- **Reuse detection.** If a spent token shows up again, someone has a copy. We can't tell whether
  the attacker or the real user is presenting it, so we revoke the whole family: both must log in
  again. The revocation is committed *before* raising, otherwise the rollback would undo it.
- **Race safety.** `SELECT ... FOR UPDATE` locks the row, so two simultaneous refreshes with the
  same token serialise: one wins, the other sees `used_at` and is treated as reuse
  (`test_concurrent_refresh_with_same_token_cannot_fork_the_family`).

Interview questions:
- *Why not just make the JWT last 14 days?* A stolen JWT would be valid for 14 days with no way
  to revoke it. Short access tokens + revocable refresh tokens bound the damage.
- *What does reuse detection protect against, and what doesn't it?* It catches a stolen refresh
  token being used after the real client refreshed (or vice versa). It can't stop an attacker who
  steals a token and uses it before the real client ever does, until the real client refreshes.
- *Two browser tabs refresh at the same moment and the user gets logged out. Why, and what would
  you change?* Both presented the same token; the second counts as reuse. Fix: a short grace
  window where the parent may be reused once and returns the already-issued child.

### 10.2 Cron schedules that fire exactly once (#2)
Read in this order: `domain/schedule.py` → `worker/scheduler.py` → the index
`uq_workflow_executions_schedule` in `db/models.py` → `tests/integration/test_scheduler.py`.

- **Pure time logic.** `due_fire_time` answers one question: what is the latest scheduled time at
  or before *now*, and should it still fire? It's pure, so every edge case (outage, workflow created
  mid-minute, daily job checked too late) is a fast unit test in `tests/domain/test_schedule.py`.
- **Misfire policy.** After an outage we fire the latest slot once, and skip anything older than the
  lookback (5 min). Replaying 60 missed "every minute" runs is almost never what users want.
- **The database is the lock.** Every worker runs the tick. Each computes the same `scheduled_for`
  and does `INSERT … ON CONFLICT DO NOTHING RETURNING id` against a *partial unique index*. Exactly
  one insert returns a row; only that worker enqueues the run. No leader election, no Redis lock
  with a TTL that might expire mid-work, and it survives crashes and restarts.
- **Enqueue after commit** (same trade-off as `dispatch_event`): a crash between commit and enqueue
  leaves a pending execution that `scripts/requeue_pending.py` recovers, never a duplicate.
- **Validate at save time.** A cron run has no triggering task, so steps that need one are rejected
  with 422 when the workflow is saved, not discovered at 3 a.m. when it fails.

Interview questions:
- *You run 3 workers and each has a scheduler. How do you stop a job running 3 times?* Make the
  run's identity deterministic (`workflow id + scheduled time`) and let a unique constraint pick
  one winner. Alternatives: leader election, or a Redis `SET NX` lock (weaker: TTL expiry, failover).
- *Why a partial index (`WHERE scheduled_for IS NOT NULL`)?* Event-triggered executions have no
  scheduled time; the index should only constrain scheduled ones, and it stays smaller.
- *The scheduler was down for two hours. What happens?* Nothing replays; the next tick fires the
  latest due slot if it's within the lookback. Explain why that's the right default and when you'd
  choose "catch up everything" instead (e.g. billing runs).
