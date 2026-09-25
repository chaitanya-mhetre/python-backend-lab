# ADR 0004: Enqueue jobs after commit (and accept the small gap)

**Status:** accepted (m4) · superseded in `production-fastapi` by a transactional outbox

## Context
When a task changes we must (1) commit the change and (2) enqueue a `dispatch_event` job in
Redis. These are two different systems, so there is no shared transaction:
- enqueue **before** commit: if the commit fails, a worker runs for a change that never happened;
- enqueue **after** commit: if the process dies between the two, the event is lost.

## Decision
Enqueue after commit. A lost event is rarer and less harmful than a phantom one.
Where losing work matters most (webhook deliveries, workflow executions), the *database row* is
created first in the same transaction as its cause, with status `pending`. The Redis job is only
a trigger. `scripts/requeue_pending.py` finds pending rows and enqueues them again; all jobs are
idempotent (row lock + "skip if already done"), so re-running them is safe.

## Consequences
- Simple, no extra tables or relay processes.
- A crash exactly between commit and enqueue loses the *event* (no workflow runs for it). The
  proper fix is the outbox pattern: write the event to an `outbox` table in the same transaction,
  and have a relay publish it. That's implemented in `production-fastapi`.
