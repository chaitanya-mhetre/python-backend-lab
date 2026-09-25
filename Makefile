.PHONY: help check test test-unit lint type fmt up up-all down migrate run worker bench snapshot
COMPOSE = docker compose -p flowforge

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'
check: lint type test  ## everything CI runs
test:  ## all tests (integration tests need `make up`)
	uv run pytest -q
test-unit:  ## fast tests, no Docker needed
	uv run pytest -q tests/domain tests/unit tests/contract
lint:
	uv run ruff check . && uv run ruff format --check .
type:
	uv run mypy
fmt:  ## auto-format
	uv run ruff format . && uv run ruff check --fix .
up:  ## start Postgres + Redis
	$(COMPOSE) up -d --wait postgres redis
up-all:  ## build and start the whole stack (api on :8000)
	$(COMPOSE) --profile app up -d --build --wait
down:
	$(COMPOSE) --profile app down
migrate:
	uv run alembic upgrade head
run:  ## API with auto-reload on :8000
	FLOWFORGE_LOG_JSON=false uv run uvicorn flowforge.main:app --reload
worker:  ## background worker
	FLOWFORGE_LOG_JSON=false uv run arq flowforge.worker.settings.WorkerSettings
bench:  ## measure task-list latency (writes docs/benchmarks/)
	uv run python scripts/bench_tasks.py
snapshot:  ## accept an intended OpenAPI change
	UPDATE_SNAPSHOT=1 uv run pytest -q tests/contract
