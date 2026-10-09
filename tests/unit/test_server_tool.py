"""Unit tests for the MCP `query` tool function in server.py.

These exercise the tool-level guards (initialization, parameter validation,
query-level rate limiting) without entering the lifespan / touching a database.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import pg_mcp.server as server_module
from pg_mcp.models.query import QueryResponse
from pg_mcp.resilience.rate_limiter import MultiRateLimiter
from pg_mcp.server import query


@pytest.fixture
def mock_orchestrator(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Install a mocked orchestrator into the server module global."""
    orchestrator = AsyncMock()
    orchestrator.execute_query.return_value = QueryResponse(
        success=True,
        generated_sql="SELECT 1;",
        validation=None,
        data=None,
        error=None,
        confidence=100,
        tokens_used=12,
    )
    monkeypatch.setattr(server_module, "_orchestrator", orchestrator)
    return orchestrator


@pytest.fixture
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install minimal settings for the rate-limit wait timeout."""
    settings = MagicMock()
    settings.resilience.rate_limit_wait_timeout = 0.0
    monkeypatch.setattr(server_module, "_settings", settings)
    return settings


class TestQueryToolGuards:
    """Tests for tool-level request validation."""

    async def test_not_initialized_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without an orchestrator the tool reports SERVER_NOT_INITIALIZED."""
        monkeypatch.setattr(server_module, "_orchestrator", None)
        result: dict[str, Any] = await query(question="test")
        assert result["success"] is False
        assert result["error"]["code"] == "SERVER_NOT_INITIALIZED"

    @pytest.mark.parametrize("return_type", ["bogus", ""])
    async def test_invalid_return_type_rejected(
        self, mock_orchestrator: AsyncMock, return_type: str
    ) -> None:
        """Unknown return_type values are rejected without calling the pipeline."""
        result = await query(question="test", return_type=return_type)
        assert result["success"] is False
        assert result["error"]["code"] == "INVALID_PARAMETER"
        mock_orchestrator.execute_query.assert_not_called()


class TestQueryToolRateLimit:
    """Tests for the query-level rate limiter inside the tool."""

    async def test_rate_limit_exceeded_returns_error(
        self,
        mock_orchestrator: AsyncMock,
        mock_settings: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no query slot frees up in time, the tool returns rate_limit_exceeded."""
        rate_limiter = MultiRateLimiter(query_limit=1, llm_limit=1)
        monkeypatch.setattr(server_module, "_rate_limiter", rate_limiter)

        # Exhaust the only query slot; wait timeout is 0.0 so the next call fails
        async with rate_limiter.for_queries(timeout=1.0):
            result = await query(question="test")
            assert result["success"] is False
            assert result["error"]["code"] == "rate_limit_exceeded"
            mock_orchestrator.execute_query.assert_not_called()

    async def test_request_passes_when_slot_available(
        self,
        mock_orchestrator: AsyncMock,
        mock_settings: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With a free slot the request flows through to the orchestrator."""
        rate_limiter = MultiRateLimiter(query_limit=1, llm_limit=1)
        monkeypatch.setattr(server_module, "_rate_limiter", rate_limiter)

        result = await query(question="test")
        assert result["success"] is True
        assert result["tokens_used"] == 12
        mock_orchestrator.execute_query.assert_called_once()
