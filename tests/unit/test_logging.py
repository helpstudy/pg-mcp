"""Unit tests for structured logging and sensitive data sanitization."""

import json
import logging

import pytest

from pg_mcp.observability.logging import (
    JSONFormatter,
    SensitiveDataFilter,
    TextFormatter,
    configure_logging,
    get_logger,
)


@pytest.fixture
def sanitizer() -> SensitiveDataFilter:
    """Fresh sensitive data filter."""
    return SensitiveDataFilter()


class TestSensitiveDataFilter:
    """Tests for log record sanitization."""

    def test_sanitize_dict_redacts_sensitive_keys(self, sanitizer: SensitiveDataFilter) -> None:
        """Top-level sensitive keys are redacted."""
        data = {"password": "hunter2", "api_key": "sk-123", "name": "ok"}
        sanitized = sanitizer._sanitize_dict(data)
        assert sanitized["password"] == "***REDACTED***"
        assert sanitized["api_key"] == "***REDACTED***"
        assert sanitized["name"] == "ok"
        assert "hunter2" not in str(sanitized)

    def test_sanitize_dict_is_recursive(self, sanitizer: SensitiveDataFilter) -> None:
        """Nested sensitive keys are redacted too."""
        data = {"db": {"host": "h", "password": "p"}, "items": [{"token": "t"}]}
        sanitized = sanitizer._sanitize_dict(data)
        assert sanitized["db"]["password"] == "***REDACTED***"
        assert sanitized["db"]["host"] == "h"
        assert sanitized["items"][0]["token"] == "***REDACTED***"

    def test_sanitize_key_matching_is_case_insensitive(
        self, sanitizer: SensitiveDataFilter
    ) -> None:
        """PASSWORD / ApiKey style keys are also redacted."""
        data = {"PASSWORD": "x", "ApiKey": "y"}
        sanitized = sanitizer._sanitize_dict(data)
        assert sanitized["PASSWORD"] == "***REDACTED***"
        assert sanitized["ApiKey"] == "***REDACTED***"

    def test_filter_redacts_extra_fields(self, sanitizer: SensitiveDataFilter) -> None:
        """Extra fields on a log record are sanitized in place."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="connecting",
            args=(),
            exc_info=None,
        )
        record.db_config = {"password": "secret-pw", "host": "localhost"}  # type: ignore[attr-defined]
        assert sanitizer.filter(record) is True
        sanitized = record.__dict__["db_config"]
        assert sanitized["password"] == "***REDACTED***"
        assert sanitized["host"] == "localhost"

    def test_filter_redacts_sensitive_record_attributes(
        self, sanitizer: SensitiveDataFilter
    ) -> None:
        """A record attribute named e.g. 'token' is redacted directly."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="auth",
            args=(),
            exc_info=None,
        )
        record.token = "bearer-abc"  # type: ignore[attr-defined]
        sanitizer.filter(record)
        assert record.__dict__["token"] == "***REDACTED***"


class TestFormatters:
    """Tests for JSON and text formatters."""

    def _record(self, msg: str = "hello") -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        record.request_id = "req-123"  # type: ignore[attr-defined]
        return record

    def test_json_formatter_output(self) -> None:
        """JSONFormatter emits parseable JSON with request_id."""
        output = JSONFormatter().format(self._record())
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "hello"
        assert parsed["request_id"] == "req-123"

    def test_text_formatter_output(self) -> None:
        """TextFormatter includes message and request_id."""
        output = TextFormatter().format(self._record())
        assert "hello" in output
        assert "[request_id=req-123]" in output
        assert "test.logger" in output


class TestConfigureLogging:
    """Tests for logging configuration."""

    def test_configure_logging_installs_handler_and_filter(self) -> None:
        """configure_logging replaces handlers and attaches the sanitizer."""
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        try:
            configure_logging(level="DEBUG", log_format="json", enable_sensitive_filter=True)
            assert len(root.handlers) == 1
            handler = root.handlers[0]
            assert isinstance(handler.formatter, JSONFormatter)
            assert any(isinstance(f, SensitiveDataFilter) for f in handler.filters)
            assert root.level == logging.DEBUG
        finally:
            root.handlers[:] = original_handlers

    def test_get_logger_returns_logger(self) -> None:
        """get_logger returns a logging.Logger instance."""
        assert isinstance(get_logger("some.name"), logging.Logger)
