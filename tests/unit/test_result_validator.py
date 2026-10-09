"""Unit tests for the LLM-based result validator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pg_mcp.config.settings import OpenAIConfig, ValidationConfig
from pg_mcp.models.errors import LLMError, LLMTimeoutError, LLMUnavailableError
from pg_mcp.models.query import ResultValidationResult
from pg_mcp.services.result_validator import ResultValidator


def _make_validator(enabled: bool = True) -> ResultValidator:
    """Build a validator with test configuration."""
    return ResultValidator(
        openai_config=OpenAIConfig(api_key="sk-test123"),
        validation_config=ValidationConfig(enabled=enabled),
    )


def _mock_response(content: str | None) -> MagicMock:
    """Build a mock ChatCompletion response."""
    response = MagicMock()
    response.usage = None
    if content is None:
        response.choices = []
    else:
        choice = MagicMock()
        choice.message.content = content
        response.choices = [choice]
    return response


VALID_JSON = json.dumps({"confidence": 85, "explanation": "Results match", "suggestion": None})


class TestResultValidator:
    """Tests for ResultValidator.validate."""

    async def test_disabled_returns_perfect_confidence(self) -> None:
        """When validation is disabled, confidence 100 with no LLM call."""
        validator = _make_validator(enabled=False)
        result = await validator.validate(
            question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1
        )
        assert isinstance(result, ResultValidationResult)
        assert result.confidence == 100
        assert result.is_acceptable is True

    @pytest.mark.parametrize(
        "confidence,expected_acceptable",
        [(85, True), (70, True), (69, False), (10, False)],
    )
    async def test_confidence_threshold(self, confidence: int, expected_acceptable: bool) -> None:
        """is_acceptable reflects the configured confidence threshold."""
        validator = _make_validator()
        payload = json.dumps({"confidence": confidence, "explanation": "ok", "suggestion": None})
        with patch.object(
            validator.client.chat.completions,
            "create",
            new=AsyncMock(return_value=_mock_response(payload)),
        ):
            result = await validator.validate(
                question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1
            )
        assert result.confidence == confidence
        assert result.is_acceptable is expected_acceptable

    async def test_invalid_json_returns_moderate_confidence(self) -> None:
        """Unparseable LLM output yields confidence 60, not acceptable."""
        validator = _make_validator()
        with patch.object(
            validator.client.chat.completions,
            "create",
            new=AsyncMock(return_value=_mock_response("not json {")),
        ):
            result = await validator.validate(
                question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1
            )
        assert result.confidence == 60
        assert result.is_acceptable is False

    async def test_empty_choices_raises_llm_error(self) -> None:
        """An empty choices list raises LLMError."""
        validator = _make_validator()
        with (
            patch.object(
                validator.client.chat.completions,
                "create",
                new=AsyncMock(return_value=_mock_response(None)),
            ),
            pytest.raises(LLMError, match="empty response"),
        ):
            await validator.validate(question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1)

    async def test_empty_content_raises_llm_error(self) -> None:
        """An empty message content raises LLMError."""
        validator = _make_validator()
        with (
            patch.object(
                validator.client.chat.completions,
                "create",
                new=AsyncMock(return_value=_mock_response("")),
            ),
            pytest.raises(LLMError, match="empty message content"),
        ):
            await validator.validate(question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1)

    async def test_timeout_raises_llm_timeout(self) -> None:
        """A TimeoutError from the API is converted to LLMTimeoutError."""
        validator = _make_validator()
        with (
            patch.object(
                validator.client.chat.completions,
                "create",
                new=AsyncMock(side_effect=TimeoutError("timed out")),
            ),
            pytest.raises(LLMTimeoutError),
        ):
            await validator.validate(question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1)

    async def test_auth_error_raises_llm_unavailable(self) -> None:
        """Authentication failures are converted to LLMUnavailableError."""
        validator = _make_validator()
        with (
            patch.object(
                validator.client.chat.completions,
                "create",
                new=AsyncMock(side_effect=Exception("api_key invalid: authentication")),
            ),
            pytest.raises(LLMUnavailableError, match="authentication"),
        ):
            await validator.validate(question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1)

    async def test_rate_limit_error_raises_llm_unavailable(self) -> None:
        """Rate limit failures are converted to LLMUnavailableError."""
        validator = _make_validator()
        with (
            patch.object(
                validator.client.chat.completions,
                "create",
                new=AsyncMock(side_effect=Exception("rate_limit exceeded")),
            ),
            pytest.raises(LLMUnavailableError, match="rate limit"),
        ):
            await validator.validate(question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1)

    async def test_generic_error_raises_llm_error(self) -> None:
        """Unexpected failures are wrapped in LLMError."""
        validator = _make_validator()
        with (
            patch.object(
                validator.client.chat.completions,
                "create",
                new=AsyncMock(side_effect=RuntimeError("connection reset")),
            ),
            pytest.raises(LLMError, match="Result validation failed"),
        ):
            await validator.validate(question="q", sql="SELECT 1", results=[{"a": 1}], row_count=1)
