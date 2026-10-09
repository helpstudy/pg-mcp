"""Unit tests for request tracing and context propagation."""

import logging

import pytest

from pg_mcp.observability.tracing import (
    TracingLogger,
    clear_request_id,
    generate_request_id,
    get_request_id,
    get_tracing_logger,
    request_context,
    set_request_id,
    trace_async,
    trace_sync,
)


class TestRequestContext:
    """Tests for request ID context management."""

    def test_generate_request_id_is_uuid(self) -> None:
        """Generated IDs are UUID4 strings."""
        request_id = generate_request_id()
        assert len(request_id) == 36
        assert request_id.count("-") == 4

    def test_generate_request_id_unique(self) -> None:
        """Two generated IDs differ."""
        assert generate_request_id() != generate_request_id()

    def test_set_and_get_request_id(self) -> None:
        """set_request_id stores the value retrievable via get_request_id."""
        set_request_id("custom-id")
        try:
            assert get_request_id() == "custom-id"
        finally:
            clear_request_id()
        assert get_request_id() is None

    async def test_request_context_generates_id(self) -> None:
        """request_context generates and yields a fresh request ID."""
        assert get_request_id() is None
        async with request_context() as request_id:
            assert request_id
            assert get_request_id() == request_id
        assert get_request_id() is None

    async def test_request_context_restores_previous(self) -> None:
        """Nested/exiting contexts restore the prior request ID."""
        async with request_context() as outer_id:
            async with request_context() as inner_id:
                assert inner_id != outer_id
                assert get_request_id() == inner_id
            assert get_request_id() == outer_id

    async def test_request_context_accepts_explicit_id(self) -> None:
        """request_context uses a caller-provided request ID."""
        async with request_context("my-fixed-id") as request_id:
            assert request_id == "my-fixed-id"
            assert get_request_id() == "my-fixed-id"


class TestTraceDecorators:
    """Tests for trace_async / trace_sync decorators."""

    async def test_trace_async_executes_without_context(self) -> None:
        """Decorator passes through when no request context is active."""

        @trace_async(operation="op")
        async def work(value: int) -> int:
            return value * 2

        assert await work(21) == 42

    async def test_trace_sync_executes_without_context(self) -> None:
        """Decorator passes through when no request context is active."""

        @trace_sync(operation="op")
        def work(value: int) -> int:
            return value + 1

        assert work(1) == 2

    async def test_trace_async_inside_context(self) -> None:
        """Decorator runs the function and preserves its return value."""

        @trace_async(operation="op")
        async def work(value: int) -> int:
            return value * 2

        async with request_context():
            assert await work(3) == 6


class TestTracingLogger:
    """Tests for the request-context-aware logger wrapper."""

    def test_get_tracing_logger(self) -> None:
        """get_tracing_logger returns a TracingLogger."""
        assert isinstance(get_tracing_logger("test.tracing"), TracingLogger)

    async def test_logger_adds_request_id_to_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        """Inside a request context, records carry the request_id field."""
        logger = get_tracing_logger("test.tracing.extra")

        with caplog.at_level(logging.INFO, logger="test.tracing.extra"):
            async with request_context("fixed-id-42"):
                logger.info("doing work", extra={"custom": "value"})

        record = caplog.records[0]
        assert record.request_id == "fixed-id-42"  # type: ignore[attr-defined]
        assert record.custom == "value"  # type: ignore[attr-defined]

    async def test_logger_respects_explicit_request_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An explicitly provided request_id is not overwritten."""
        logger = get_tracing_logger("test.tracing.explicit")

        with caplog.at_level(logging.INFO, logger="test.tracing.explicit"):
            async with request_context("ctx-id"):
                logger.info("msg", extra={"request_id": "explicit-id"})

        record = caplog.records[0]
        assert record.request_id == "explicit-id"  # type: ignore[attr-defined]
