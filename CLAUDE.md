# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PostgreSQL MCP Server (`pg-mcp`): a FastMCP server that converts natural-language questions into SQL (OpenAI), validates them with sqlglot, executes them read-only against PostgreSQL (asyncpg), and optionally LLM-validates results with a confidence score. Exposes a single MCP tool, `query`. Uses `uv` for dependency management; Python 3.14 (note: ruff/mypy config targets 3.11).

## Commands

```bash
uv sync --all-extras            # install dependencies (incl. dev)
uv run pytest                   # all tests
uv run pytest tests/unit/test_sql_validator.py                     # single file
uv run pytest tests/unit/test_sql_executor.py::TestX::test_y -v    # single test
uv run pytest -m integration    # only integration-marked tests
uv run pytest --cov=src --cov-report=html   # coverage (branch, fail_under=80)
uv run mypy src                 # type check (strict)
uv run ruff check --fix . && uv run ruff format .
uv run python -m pg_mcp         # run the MCP server (stdio transport); console script: uv run pg-mcp
cd fixtures && make create-all  # load test databases via psql (blog_small, ecommerce_medium, saas_crm_large)
```

**Entry point**: `main.py` simply delegates to `pg_mcp.__main__:main` (same as the `pg-mcp` console script). `uv run python -m pg_mcp` also works. All three are equivalent.

## Architecture

```
MCP client ⇄ stdio ⇄ FastMCP (server.py)
                         │  tool: query(question, database?, return_type: "sql"|"result")
                         ▼
              QueryOrchestrator (services/orchestrator.py)
    ┌──────────────┬───────────────┬──────────────┬──────────────────┐
    ▼              ▼               ▼              ▼                  ▼
SchemaCache    SQLGenerator    SQLValidator   SQLExecutor      ResultValidator
(cache/)       (OpenAI via     (sqlglot       (asyncpg,        (LLM, confidence
TTL cache,     prompts/)       parse:         read-only        score; NON-BLOCKING —
db/            Retry with      SELECT-only,   transaction,     its failure never fails
introspection  error feedback  blocked fns,   row limit,       the request; defaults
               on validation   tables/cols)   timeout)         to confidence 100)
               failure)
```

Key structural points (things you can't see from one file):

- **Lifespan-wired globals**: `server.py` holds module-level globals (`_settings`, `_pools`, `_schema_cache`, `_orchestrator`, `_metrics`, `_rate_limiter`) constructed inside the FastMCP `lifespan()` context manager. The `query` tool reads the global `_orchestrator`. Component wiring order, schema auto-refresh (`CACHE_AUTO_REFRESH_MINUTES > 0`), metrics HTTP server, and shutdown (pool close with timeout — see SHUTDOWN_FIX.md) all live in `lifespan()`.
- **Retry-with-feedback loop**: `QueryOrchestrator._generate_sql_with_retry()` passes `previous_sql` + `error_feedback` back into `SQLGenerator.generate()` on validation failure, up to `RESILIENCE_MAX_RETRIES`, with exponential backoff between attempts. LLM calls are guarded by a `CircuitBreaker` (orchestrator-internal). `MultiRateLimiter` gates throughput at TWO levels: query-level via `for_queries()` inside the `query` tool (`TimeoutError` → `RATE_LIMIT_EXCEEDED` response), LLM-level via `for_llm()` inside `_generate_with_limiter()` (`TimeoutError` → `RateLimitExceededError`).
- **Multi-database config**: `Settings.databases` is a `dict[str, DatabaseConfig]`. If the `DATABASES` env var (JSON mapping alias → connection fields) is set it replaces the single `DATABASE_*` config; otherwise it defaults to `{database.name: database}` (kept in sync by a model_validator). Pools, executors, and schemas are all keyed by db alias; `QueryOrchestrator` routes each request to `sql_executors[database_name]` and fails closed with `DatabaseError` when a pool has no matching executor. `_resolve_database()` auto-selects the only DB when `database=None`, and rejects ambiguous requests when multiple DBs exist.
- **Security policy is config-driven**: `SECURITY_BLOCKED_TABLES` / `SECURITY_BLOCKED_COLUMNS` (comma-separated, support `schema.table` / `table.column`) and `SECURITY_ALLOW_EXPLAIN` flow from `SecurityConfig` into `SQLValidator`. Blocked-column matching fails closed: alias-qualified (`u.password_hash`) and unqualified references to blocked columns are also rejected. There is no write-mode toggle — non-SELECT statements are always rejected.
- **Metrics & tracing are wired into the request path**: `QueryOrchestrator.execute_query()` opens a `request_context()` (contextvar request_id), records `query_requests`/`query_duration` per request, `db_query_duration` per execution, `sql_rejected` on security errors; LLM calls record `llm_calls`/`llm_latency`/`llm_tokens_used` in both orchestrator (generate_sql) and ResultValidator (validate_result). `MetricsCollector` owns a private `CollectorRegistry` (exposed as `.registry`) so `reset_all_metrics()` is safe; the metrics HTTP server is started via `MetricsCollector.start_metrics_server()`.
- **Errors never escape the tool**: `query()` catches everything and returns `{success: false, error: {code, message, details}}`. Domain errors derive from `PgMcpError` in `models/errors.py` with an `ErrorCode` enum.
- **`SQLGenerator.generate()` returns `SQLGenerationResult`** (`sql` + `tokens_used` from the OpenAI usage payload), not a bare string; `QueryResponse.to_dict()` guarantees `tokens_used` defaults to 0 in the tool output.

### Module map

- `src/pg_mcp/server.py` — FastMCP app, lifespan wiring, `query` tool (MCP server uses the `mcp` package's `mcp.server.fastmcp`, not the `fastmcp` package)
- `services/` — orchestrator; `sql_generator.py` (OpenAI); `sql_validator.py` (sqlglot security validation — allowed/forbidden statement types and dangerous functions are `ClassVar` sets at the top of the file); `sql_executor.py` (read-only transaction, max rows, timeout); `result_validator.py`
- `cache/schema_cache.py` — TTL schema cache + optional auto-refresh (`start_auto_refresh(interval_minutes, pools)`, driven by `CACHE_AUTO_REFRESH_MINUTES`); loads via `db/introspection.py`
- `db/pool.py` — asyncpg pools `create_pools`/`close_pools` (multi-db, dict-keyed; close has a timeout to avoid shutdown hang)
- `models/` — `errors.py` (exception hierarchy + `ErrorCode`), `query.py` (request/response/result models, `ReturnType`, `SQLGenerationResult`), `schema.py`
- `config/settings.py` — pydantic-settings groups, each with an env prefix: `DATABASE_`, `OPENAI_`, `SECURITY_`, `VALIDATION_`, `CACHE_`, `RESILIENCE_`, `OBSERVABILITY_`, plus `DATABASES` (JSON dict for multi-db). Loaded from `.env` (see `.env.example`). Has `reset_settings()` used by tests.
- `prompts/` — LLM prompt templates (`sql_generation`, `result_validation`)
- `resilience/` — `CircuitBreaker`, `MultiRateLimiter` (`for_queries`/`for_llm` async context managers raising `TimeoutError` when no slot frees up)
- `observability/` — structured logging with sensitive-data filter (`SensitiveDataFilter`); `metrics.py` (`MetricsCollector` singleton, private registry); `tracing.py` (`request_context()` contextvar request_id, `TracingLogger`)

SQL parsing/validation uses **sqlglot** (not pglast).

## Testing

- `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio` decorations (integration tests still carry them).
- Default addopts include `-m "not integration"` — integration/e2e tests are skipped unless you run `uv run pytest -m integration` (or override `-m ""`).
- `tests/conftest.py` has autouse fixtures that reset settings before each test and force `OBSERVABILITY_METRICS_ENABLED=false` (prevents port conflicts).
- `tests/unit/` — mocked, run anywhere (config, models, validator, generator, executor, orchestrator, resilience, cache, tracing, logging, metrics, result_validator).
- `tests/security/` — security-focused unit tests: blocked tables/columns enforcement (incl. alias/schema-qualified bypasses), EXPLAIN policy, multi-database isolation (request routing fails closed).
- `tests/integration/` and `tests/e2e/` enter `lifespan(mcp)` directly and call the `query` tool function — these need a live PostgreSQL (matching `DATABASE_*` env) and a valid `OPENAI_API_KEY`.
- `fixtures/` — SQL scripts + Makefile to create three test databases (small blog / medium e-commerce / large SaaS CRM) with `psql`.

## Conventions

- Full type hints and Google-style docstrings throughout; mypy runs strict.
- Ruff with bandit rules (`S`): no SQL string interpolation for identifiers, no bare `except`; `tests/**` allows assert/hardcoded test credentials.
- Never log secrets or PII — use `safe_dsn`-style masking; the logging layer has a sensitive-data filter.
- Commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `perf:`, `security:`.
