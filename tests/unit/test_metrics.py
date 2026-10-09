"""Unit tests for the Prometheus metrics collector."""

from prometheus_client import generate_latest

from pg_mcp.observability.metrics import MetricsCollector, metrics


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_singleton_identity(self) -> None:
        """MetricsCollector returns the same instance."""
        assert MetricsCollector() is metrics
        assert MetricsCollector() is MetricsCollector()

    def test_increment_query_request(self) -> None:
        """Query request counter accepts status/database labels (delta-based)."""
        labels = metrics.query_requests.labels(status="success", database="testdb")
        before = labels._value.get()  # type: ignore[union-attr]
        metrics.increment_query_request(status="success", database="testdb")
        assert labels._value.get() - before == 1  # type: ignore[union-attr]

    def test_llm_helpers(self) -> None:
        """LLM call/latency/token helpers record values.

        Counters are cumulative across the test session, so assert on deltas.
        """
        calls_before = metrics.llm_calls.labels(operation="generate_sql")._value.get()
        tokens_before = metrics.llm_tokens_used.labels(operation="generate_sql")._value.get()

        metrics.increment_llm_call("generate_sql")
        metrics.observe_llm_latency("generate_sql", 0.42)
        metrics.increment_llm_tokens("generate_sql", 123)

        calls = metrics.llm_calls.labels(operation="generate_sql")._value.get()
        tokens = metrics.llm_tokens_used.labels(operation="generate_sql")._value.get()
        assert calls - calls_before == 1
        assert tokens - tokens_before == 123

    def test_sql_rejected_helper(self) -> None:
        """SQL rejection counter accepts a reason label."""
        before = metrics.sql_rejected.labels(reason="security_violation")._value.get()
        metrics.increment_sql_rejected("security_violation")
        after = metrics.sql_rejected.labels(reason="security_violation")._value.get()
        assert after - before == 1

    def test_db_helpers(self) -> None:
        """Database connection/duration helpers record values."""
        metrics.set_db_connections_active("testdb", 4)
        metrics.observe_db_query_duration(0.05)
        metrics.set_schema_cache_age("testdb", 120.0)

        gauge = metrics.db_connections_active.labels(database="testdb")._value.get()
        age = metrics.schema_cache_age.labels(database="testdb")._value.get()
        assert gauge == 4
        assert age == 120.0

    def test_metrics_exposed_via_registry(self) -> None:
        """All metric families appear in the collector's registry exposition."""
        exposition = generate_latest(metrics.registry)
        for name in (
            b"pg_mcp_query_requests_total",
            b"pg_mcp_query_duration_seconds",
            b"pg_mcp_llm_calls_total",
            b"pg_mcp_llm_latency_seconds",
            b"pg_mcp_llm_tokens_used",
            b"pg_mcp_sql_rejected_total",
            b"pg_mcp_db_connections_active",
            b"pg_mcp_db_query_duration_seconds",
            b"pg_mcp_schema_cache_age_seconds",
        ):
            assert name in exposition, f"missing metric family: {name!s}"

    def test_reset_all_metrics(self) -> None:
        """Reset clears label sets and keeps metrics usable afterwards."""
        metrics.increment_query_request(status="success", database="testdb")
        metrics.reset_all_metrics()

        # Old label set is gone; exposition reflects a fresh state
        exposition = generate_latest(metrics.registry)
        assert b'status="success",database="testdb"' not in exposition.replace(b" ", b"")

        # Metrics remain usable after reset
        metrics.increment_query_request(status="error", database="testdb")
        value = (
            metrics.query_requests.labels(status="error", database="testdb")._value.get()  # type: ignore[union-attr]
        )
        assert value == 1
