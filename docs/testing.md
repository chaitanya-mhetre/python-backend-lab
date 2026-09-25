# Testing strategy

| Layer | Where | Needs Docker | What it proves |
|---|---|---|---|
| Domain | `tests/domain` | no | state machine, repository protocol, paginate generator, sync engine (loops, failures) |
| Unit | `tests/unit` | no | permission matrix (every role × action), `@requires`, API key format, webhook signing, SSRF rules, retry schedule |
| Contract | `tests/contract` | no | OpenAPI schema unchanged unless intended |
| Integration | `tests/integration` | yes (`make up`) | real Postgres + Redis through the HTTP API and the worker functions |

Run everything with `make check`, or the Docker-free part with `make test-unit`.
Integration tests **skip** (never fake) when Postgres isn't reachable.

## Techniques worth knowing
- **Real database, migrated with Alembic** each session (`downgrade base` → `upgrade head`), so
  migrations are tested too. Tables are truncated after every test.
- **Deterministic background jobs:** the app gets a `RecordingJobQueue`; tests call `drain()` to
  run queued jobs like a worker would. One test (`test_arq_worker.py`) uses a *real* arq worker
  against Redis to prove the wiring.
- **Crash simulation:** a step handler raises a `BaseException` subclass after step 1 committed;
  re-running the execution must not repeat step 1 (`test_worker_crash_mid_execution…`).
- **Concurrency:** 5 concurrent PATCHes with the same `If-Match` → exactly one 200, four 409s.
  20 concurrent requests against a bucket of 5 → exactly 5 pass (proves the Lua script is atomic).
- **Frozen clock:** worker deps take a `clock` function; webhook retry times are asserted exactly.
- **HTTP mocking:** `respx` intercepts outgoing webhook calls; signatures are verified with the
  same code a receiver would use.
- **DNS faking:** the SSRF guard takes an injectable resolver, so private-IP rules are tested
  without real DNS.
