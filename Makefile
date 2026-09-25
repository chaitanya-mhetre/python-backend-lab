.PHONY: check test test-unit lint type fmt up down migrate run worker
COMPOSE = docker compose -p flowforge

check: lint type test
test:
	uv run pytest -q
test-unit:
	uv run pytest -q tests/domain
lint:
	uv run ruff check .
type:
	uv run mypy
fmt:
	uv run ruff format . && uv run ruff check --fix .
up:
	$(COMPOSE) up -d --wait postgres redis
down:
	$(COMPOSE) down
migrate:
	uv run alembic upgrade head
run:
	uv run uvicorn flowforge.main:app --reload
