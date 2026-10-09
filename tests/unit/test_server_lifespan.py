"""Unit tests for server.py lifespan assembly (IMPROVEMENT_PLAN task 5.5).

Mocks all external boundaries (PostgreSQL pools, OpenAI client, metrics HTTP
server) and asserts that the lifespan wires up components in the right order
and shuts them down gracefully.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import pg_mcp.server as server_module
from pg_mcp.server import lifespan


@pytest.fixture
def lifespan_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide required env vars and reset module globals for lifespan tests."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("DATABASE_NAME", "testdb")
    for attr in (
        "_settings",
        "_pools",
        "_schema_cache",
        "_orchestrator",
        "_metrics",
        "_rate_limiter",
    ):
        monkeypatch.setattr(server_module, attr, None, raising=False)


@pytest.fixture
def mock_boundaries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mock every external component the lifespan wires together."""
    pool = MagicMock()
    pools = {"testdb": pool}

    mocks: dict[str, Any] = {}

    create_pools = AsyncMock(return_value=pools)
    close_pools = AsyncMock()
    monkeypatch.setattr(server_module, "create_pools", create_pools)
    monkeypatch.setattr(server_module, "close_pools", close_pools)
    mocks["create_pools"] = create_pools
    mocks["close_pools"] = close_pools

    schema = MagicMock()
    schema.tables = {"users": MagicMock()}
    schema_cache = MagicMock()
    schema_cache.load = AsyncMock(return_value=schema)
    schema_cache.start_auto_refresh = AsyncMock()
    schema_cache.stop_auto_refresh = AsyncMock()
    monkeypatch.setattr(server_module, "SchemaCache", MagicMock(return_value=schema_cache))
    mocks["schema_cache"] = schema_cache

    orchestrator = MagicMock()
    monkeypatch.setattr(server_module, "QueryOrchestrator", MagicMock(return_value=orchestrator))
    mocks["orchestrator"] = orchestrator

    metrics = MagicMock()
    monkeypatch.setattr(server_module, "MetricsCollector", MagicMock(return_value=metrics))
    mocks["metrics"] = metrics

    executor_cls = MagicMock()
    monkeypatch.setattr(server_module, "SQLExecutor", executor_cls)
    mocks["executor_cls"] = executor_cls

    rate_limiter = MagicMock()
    rate_limiter_cls = MagicMock(return_value=rate_limiter)
    monkeypatch.setattr(server_module, "MultiRateLimiter", rate_limiter_cls)
    mocks["rate_limiter"] = rate_limiter
    mocks["rate_limiter_cls"] = rate_limiter_cls

    mocks["configure_logging"] = MagicMock()
    monkeypatch.setattr(server_module, "configure_logging", mocks["configure_logging"])

    return mocks


class TestLifespanAssembly:
    """Tests for component wiring inside the lifespan context."""

    @pytest.mark.usefixtures("lifespan_env")
    async def test_startup_wires_all_components(self, mock_boundaries: dict[str, Any]) -> None:
        """Startup creates pools, schema cache, executors, limiter and orchestrator."""
        async with lifespan(server_module.mcp):
            # Pools created from the configured databases
            mock_boundaries["create_pools"].assert_awaited_once()

            # Schema loaded for every pool (keyed by the configured db name)
            assert mock_boundaries["schema_cache"].load.await_count == 1
            assert mock_boundaries["schema_cache"].load.call_args.args[0] == "testdb"

            # One executor per database
            assert mock_boundaries["executor_cls"].call_count == 1

            # Rate limiter and orchestrator created
            assert (
                mock_boundaries["rate_limiter_cls"].call_args.kwargs["query_limit"] == 10
            )  # default RESILIENCE_QUERY_RATE_LIMIT

            # Globals populated for the query tool
            assert server_module._pools is not None
            assert server_module._orchestrator is mock_boundaries["orchestrator"]
            assert server_module._schema_cache is mock_boundaries["schema_cache"]
            assert server_module._metrics is mock_boundaries["metrics"]

    @pytest.mark.usefixtures("lifespan_env")
    async def test_shutdown_closes_pools_with_timeout(
        self, mock_boundaries: dict[str, Any]
    ) -> None:
        """Shutdown closes the connection pools gracefully with a timeout."""
        async with lifespan(server_module.mcp):
            pass
        mock_boundaries["close_pools"].assert_awaited_once()
        kwargs = mock_boundaries["close_pools"].call_args.kwargs
        assert kwargs.get("timeout") == 5.0
        # Auto-refresh was never enabled (default CACHE_AUTO_REFRESH_MINUTES=0)
        mock_boundaries["schema_cache"].start_auto_refresh.assert_not_called()

    @pytest.mark.usefixtures("lifespan_env")
    async def test_shutdown_stops_auto_refresh_when_enabled(
        self, mock_boundaries: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When auto-refresh is configured, shutdown stops the refresh task."""
        monkeypatch.setenv("CACHE_AUTO_REFRESH_MINUTES", "30")
        async with lifespan(server_module.mcp):
            mock_boundaries["schema_cache"].start_auto_refresh.assert_awaited_once()
        mock_boundaries["schema_cache"].stop_auto_refresh.assert_awaited_once()
