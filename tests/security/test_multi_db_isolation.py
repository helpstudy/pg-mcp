"""Security tests for multi-database isolation.

These tests verify that a request targeting one database can never touch
another database's executor or pool, and that ambiguous requests are
rejected rather than silently routed.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pg_mcp.config.settings import ResilienceConfig, ValidationConfig
from pg_mcp.models.query import QueryRequest, QueryResponse, ReturnType, SQLGenerationResult
from pg_mcp.services.orchestrator import QueryOrchestrator


def _make_orchestrator(
    executors: dict[str, Any],
    pools: dict[str, Any],
) -> tuple[QueryOrchestrator, dict[str, Any]]:
    """Build an orchestrator with mocked components and given executors/pools."""
    mock_cache = MagicMock()
    mock_cache.get.return_value = MagicMock()

    executors = {name: MagicMock(execute=AsyncMock(return_value=([], 0))) for name in executors}
    mock_generator = AsyncMock()
    mock_generator.generate.return_value = SQLGenerationResult(sql="SELECT 1;", tokens_used=None)

    orchestrator = QueryOrchestrator(
        sql_generator=mock_generator,
        sql_validator=MagicMock(),
        sql_executors=executors,
        result_validator=MagicMock(),
        schema_cache=mock_cache,
        pools=pools,
        resilience_config=ResilienceConfig(retry_delay=0.0),
        validation_config=ValidationConfig(enabled=False),
    )
    return orchestrator, executors


class TestDatabaseIsolation:
    """Tests ensuring requests cannot cross database boundaries."""

    @pytest.mark.asyncio
    async def test_request_only_touches_target_executor(self) -> None:
        """A request for db_a must not invoke db_b's executor."""
        orchestrator, executors = _make_orchestrator(
            executors=["db_a", "db_b"], pools={"db_a": MagicMock(), "db_b": MagicMock()}
        )

        request = QueryRequest(question="Test", database="db_a", return_type=ReturnType.RESULT)
        response = await orchestrator.execute_query(request)

        assert response.success is True
        executors["db_a"].execute.assert_called_once()
        executors["db_b"].execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_for_unknown_database_rejected(self) -> None:
        """A nonexistent database name is rejected, not silently rerouted."""
        orchestrator, executors = _make_orchestrator(
            executors=["db_a"], pools={"db_a": MagicMock()}
        )

        request = QueryRequest(question="Test", database="db_z", return_type=ReturnType.RESULT)
        response = await orchestrator.execute_query(request)

        assert response.success is False
        assert response.error is not None
        assert response.error.code == "database_error"
        executors["db_a"].execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambiguous_request_rejected(self) -> None:
        """With multiple databases, omitting the database name is rejected."""
        orchestrator, executors = _make_orchestrator(
            executors=["db_a", "db_b"], pools={"db_a": MagicMock(), "db_b": MagicMock()}
        )

        request = QueryRequest(question="Test", return_type=ReturnType.RESULT)
        response = await orchestrator.execute_query(request)

        assert response.success is False
        assert response.error is not None
        assert response.error.code == "database_error"
        assert "available_databases" in (response.error.details or {})
        executors["db_a"].execute.assert_not_called()
        executors["db_b"].execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_database_auto_selected(self) -> None:
        """With exactly one database, omitting the name auto-selects it."""
        orchestrator, executors = _make_orchestrator(
            executors=["only_db"], pools={"only_db": MagicMock()}
        )

        request = QueryRequest(question="Test", return_type=ReturnType.RESULT)
        response = await orchestrator.execute_query(request)

        assert response.success is True
        executors["only_db"].execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_pool_without_executor_fails_closed(self) -> None:
        """A configured pool lacking an executor fails closed (no execution)."""
        orchestrator, executors = _make_orchestrator(
            executors=["db_a"], pools={"db_a": MagicMock(), "ghost_db": MagicMock()}
        )

        request = QueryRequest(question="Test", database="ghost_db", return_type=ReturnType.RESULT)
        response: QueryResponse = await orchestrator.execute_query(request)

        assert response.success is False
        assert response.error is not None
        assert response.error.code == "database_error"
        assert "executor" in response.error.message.lower()
        executors["db_a"].execute.assert_not_called()
