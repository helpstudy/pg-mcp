.PHONY: check lint format type test test-all coverage clean

# One-shot quality gate: lint + type check + unit/security tests.
check: lint type test

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

type:
	uv run mypy src

test:
	uv run pytest

# Full run including integration/e2e (requires live PostgreSQL + OPENAI_API_KEY).
test-all:
	uv run pytest -m ""

coverage:
	uv run pytest --cov=src --cov-report=term-missing --cov-report=html

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
