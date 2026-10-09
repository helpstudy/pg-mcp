"""Security tests for blocked table and column enforcement.

These tests verify that the configured blocked_tables / blocked_columns policy
actually prevents queries from touching sensitive objects.
"""

import pytest

from pg_mcp.models.errors import SecurityViolationError
from pg_mcp.services.sql_validator import SQLValidator


class TestBlockedTables:
    """Tests for blocked table enforcement."""

    def test_direct_access_blocked(self, restricted_validator: SQLValidator) -> None:
        """Direct SELECT on a blocked table is rejected."""
        with pytest.raises(SecurityViolationError, match="table 'secrets'"):
            restricted_validator.validate_or_raise("SELECT * FROM secrets")

    def test_case_insensitive_blocked(self, restricted_validator: SQLValidator) -> None:
        """Blocked table matching is case-insensitive."""
        with pytest.raises(SecurityViolationError, match="table 'secrets'"):
            restricted_validator.validate_or_raise("SELECT * FROM SECRETS")

    def test_schema_qualified_table_blocked(self, restricted_validator: SQLValidator) -> None:
        """schema.table style access to a blocked table is rejected."""
        with pytest.raises(SecurityViolationError, match=r"internal\.passwords"):
            restricted_validator.validate_or_raise("SELECT * FROM internal.passwords")

    def test_join_with_blocked_table(self, restricted_validator: SQLValidator) -> None:
        """A JOIN referencing a blocked table is rejected."""
        with pytest.raises(SecurityViolationError, match="table 'secrets'"):
            restricted_validator.validate_or_raise(
                "SELECT u.name FROM users u JOIN secrets s ON s.user_id = u.id"
            )

    def test_subquery_with_blocked_table(self, restricted_validator: SQLValidator) -> None:
        """A subquery referencing a blocked table is rejected."""
        with pytest.raises(SecurityViolationError, match="table 'secrets'"):
            restricted_validator.validate_or_raise(
                "SELECT * FROM users WHERE id IN (SELECT user_id FROM secrets)"
            )

    def test_cte_referencing_blocked_table(self, restricted_validator: SQLValidator) -> None:
        """A CTE that selects from a blocked table is rejected."""
        with pytest.raises(SecurityViolationError, match="table 'audit_log'"):
            restricted_validator.validate_or_raise(
                "WITH recent AS (SELECT * FROM audit_log) SELECT * FROM recent"
            )

    def test_allowed_table_passes(self, restricted_validator: SQLValidator) -> None:
        """Queries on non-blocked tables are allowed."""
        is_valid, error = restricted_validator.validate("SELECT * FROM users")
        assert is_valid
        assert error is None


class TestBlockedColumns:
    """Tests for blocked column enforcement."""

    def test_plain_column_blocked(self, restricted_validator: SQLValidator) -> None:
        """Selecting a blocked column by bare name is rejected."""
        with pytest.raises(SecurityViolationError, match="column 'password_hash'"):
            restricted_validator.validate_or_raise("SELECT password_hash FROM users")

    def test_qualified_column_blocked(self, restricted_validator: SQLValidator) -> None:
        """Selecting a blocked column through a table alias is rejected."""
        with pytest.raises(SecurityViolationError, match="password_hash"):
            restricted_validator.validate_or_raise("SELECT u.password_hash FROM users u")

    def test_where_clause_column_blocked(self, restricted_validator: SQLValidator) -> None:
        """A blocked column referenced only in WHERE is rejected."""
        with pytest.raises(SecurityViolationError, match="column 'secret'"):
            restricted_validator.validate_or_raise("SELECT id FROM api_keys WHERE secret = 'x'")

    def test_unblocked_column_passes(self, restricted_validator: SQLValidator) -> None:
        """Columns not on the blocklist are allowed."""
        is_valid, error = restricted_validator.validate("SELECT name, email FROM users")
        assert is_valid
        assert error is None


class TestDefaultPolicyAllowsAllObjects:
    """With no blocked objects configured, object-level checks are skipped."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM secrets",
            "SELECT password_hash FROM users",
            "SELECT internal.passwords FROM internal.passwords",
        ],
    )
    def test_no_blocklist_means_no_object_restriction(
        self, default_validator: SQLValidator, sql: str
    ) -> None:
        """Default (empty) blocklist does not reject object access."""
        is_valid, error = default_validator.validate(sql)
        assert is_valid
        assert error is None
