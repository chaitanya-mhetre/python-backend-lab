# Contributing

## Setup
```bash
uv sync            # install dependencies (Python 3.12+)
make up            # Postgres (55432) + Redis (56379) in Docker
make migrate
make check         # ruff + mypy --strict + pytest
```

## Rules
- Every change keeps `make check` green. CI runs the same commands.
- Code layout: routers (HTTP only) → services (rules + authorization) → repositories (SQL only).
  Don't put SQL in routers or HTTP concerns in services.
- New tables/columns: change `src/flowforge/db/models.py`, then
  `uv run alembic revision --autogenerate --rev-id 00NN -m "..."`, **read the generated file**, and
  write a working `downgrade()`.
- API changes: if `tests/contract` fails, the OpenAPI schema changed. If that's intended, run
  `make snapshot` and explain the change in the PR.
- Commits: conventional style (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`), small and focused.
- Never commit secrets. `.env` is gitignored; update `.env.example` when you add a setting.
