"""Security tests for nested data-modifying statement enforcement.

PostgreSQL allows data-modifying statements inside CTEs
(``WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d``), which sqlglot
parses as a top-level ``Select``. These tests verify the validator rejects
forbidden statement types no matter where they appear in the query tree —
either as a SecurityViolationError (parsed tree scan) or a SQLParseError
(constructs sqlglot/PostgreSQL cannot even parse).
"""

import pytest

from pg_mcp.services.sql_validator import SQLValidator


class TestNestedForbiddenStatements:
    """Tests for forbidden statements nested in CTEs and subqueries."""

    @pytest.mark.parametrize(
        "nested_stmt",
        [
            "DELETE FROM users",
            "INSERT INTO users VALUES (1)",
            "UPDATE users SET name = 'x'",
            "TRUNCATE users",
        ],
        ids=["delete-cte", "insert-cte", "update-cte", "truncate-cte"],
    )
    def test_data_modifying_cte_rejected(
        self, default_validator: SQLValidator, nested_stmt: str
    ) -> None:
        """A CTE containing a data-modifying statement is never accepted."""
        sql = f"WITH d AS ({nested_stmt} RETURNING *) SELECT * FROM d"
        is_valid, _ = default_validator.validate(sql)
        assert is_valid is False

    def test_delete_inside_cte_is_blocked(self, default_validator: SQLValidator) -> None:
        """The canonical data-modifying CTE bypass is rejected with a clear message."""
        from pg_mcp.models.errors import SecurityViolationError

        with pytest.raises(SecurityViolationError, match="DELETE"):
            default_validator.validate_or_raise(
                "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d"
            )

    def test_deeply_nested_modifying_cte_rejected(self, default_validator: SQLValidator) -> None:
        """A data-modifying CTE buried inside another CTE is rejected."""
        is_valid, _ = default_validator.validate(
            """
            WITH outer_cte AS (
                WITH inner_cte AS (DELETE FROM users RETURNING *)
                SELECT * FROM inner_cte
            )
            SELECT * FROM outer_cte
            """
        )
        assert is_valid is False

    def test_invalid_from_delete_rejected(self, default_validator: SQLValidator) -> None:
        """Non-parseable attempts (e.g. DELETE as a derived table) are rejected."""
        is_valid, _ = default_validator.validate(
            "SELECT * FROM (DELETE FROM users RETURNING *) AS d"
        )
        assert is_valid is False

    def test_plain_select_with_cte_still_allowed(self, default_validator: SQLValidator) -> None:
        """Ordinary read-only CTEs keep working (no false positives)."""
        assert default_validator.validate(
            "WITH recent AS (SELECT * FROM users WHERE created_at > now() - interval '7 days') "
            "SELECT count(*) FROM recent"
        ) == (True, None)

    def test_readonly_select_still_allowed(self, default_validator: SQLValidator) -> None:
        """A plain SELECT is unaffected by the nested-statement scan."""
        assert default_validator.validate("SELECT * FROM users") == (True, None)
