"""Security tests for the EXPLAIN statement policy."""

import pytest

from pg_mcp.config.settings import SecurityConfig
from pg_mcp.models.errors import SecurityViolationError
from pg_mcp.services.sql_validator import SQLValidator


class TestExplainPolicy:
    """Tests for allow_explain configuration."""

    def test_explain_denied_by_default(self) -> None:
        """EXPLAIN is rejected unless explicitly allowed."""
        validator = SQLValidator(config=SecurityConfig())
        with pytest.raises(SecurityViolationError, match="EXPLAIN"):
            validator.validate_or_raise("EXPLAIN SELECT 1")

    def test_explain_allowed_when_enabled(self, restricted_validator: SQLValidator) -> None:
        """EXPLAIN passes when allow_explain=True."""
        is_valid, error = restricted_validator.validate("EXPLAIN SELECT * FROM users")
        assert is_valid
        assert error is None

    @pytest.mark.parametrize(
        "sql",
        [
            "COPY users TO '/tmp/out.csv'",
            "VACUUM",
            "CHECKPOINT",
        ],
    )
    def test_other_commands_still_rejected_when_explain_allowed(
        self, restricted_validator: SQLValidator, sql: str
    ) -> None:
        """Allowing EXPLAIN does not open the door to other commands."""
        with pytest.raises(SecurityViolationError):
            restricted_validator.validate_or_raise(sql)
