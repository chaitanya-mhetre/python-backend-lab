# ADR 0001: Routers → services → repositories

**Status:** accepted (m2)

## Context
FastAPI makes it easy to write SQL directly inside route functions. That's quick at first, but
business rules end up spread across HTTP handlers, can't be tested without HTTP, and can't be
reused by the background worker.

## Decision
Three layers, each with one job:
- **Routers**: parse/validate HTTP input with Pydantic, call one service method, shape the response.
- **Services**: business rules, authorization (`@requires`), audit logging, transaction commit.
- **Repositories**: the only place SQL is written.

The pure domain model (`flowforge.domain`) has no framework imports at all; services use it to
validate state changes (e.g. `Task.transition_to`), so a rule lives in exactly one place.

## Consequences
- Services are unit-testable and reusable from the worker and scripts.
- More files and some mapping code (ORM row ↔ domain object ↔ Pydantic schema).
- Authorization at the service layer means a new router can't forget the permission check.
