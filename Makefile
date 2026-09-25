.PHONY: check test lint type fmt
check: lint type test
test:
	uv run pytest -q
lint:
	uv run ruff check .
type:
	uv run mypy
fmt:
	uv run ruff format . && uv run ruff check --fix .
