"""Shared fixtures for security tests."""

import pytest

from pg_mcp.config.settings import SecurityConfig
from pg_mcp.services.sql_validator import SQLValidator


@pytest.fixture
def default_validator() -> SQLValidator:
    """Validator with default security policy (no blocked objects, no EXPLAIN)."""
    return SQLValidator(config=SecurityConfig())


@pytest.fixture
def restricted_validator() -> SQLValidator:
    """Validator with blocked tables/columns and EXPLAIN allowed."""
    return SQLValidator(
        config=SecurityConfig(
            blocked_functions=["pg_sleep", "pg_read_file"],
            blocked_tables=["secrets", "audit_log", "internal.passwords"],
            blocked_columns=["users.password_hash", "api_keys.secret"],
        ),
        blocked_tables=["secrets", "audit_log", "internal.passwords"],
        blocked_columns=["users.password_hash", "api_keys.secret"],
        allow_explain=True,
    )
