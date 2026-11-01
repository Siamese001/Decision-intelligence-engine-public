"""
Unit and acceptance test suite for Wave 3: Hermetic Sandboxed Environment & Chaos Injection.

Verifies:
- SandboxEnvironment provisioning, schema initialization, and isolated teardown.
- StateDeltaAuditor table delta accounting, positive mutations, and idempotent no-op detection.
- ChaosInjector simulated fault injection (timeouts, rate limits, truncated payloads, recovery).
"""

from __future__ import annotations

import sqlite3

import pytest

from dual_pipeline.evals.sandbox import (
    ChaosInjector,
    ChaosSimulationError,
    SandboxEnvironment,
    StateDeltaAuditor,
)


def test_sandbox_provision_and_lifecycle():
    """Verify sandbox creates clean isolated workspace with WAL SQLite schema."""
    with SandboxEnvironment() as config:
        assert config.sandbox_root.exists()
        assert config.graph_path.exists()
        assert config.output_dir.exists()
        assert config.artifacts_dir.exists()

        # Connect and check PRAGMA journal_mode
        conn = sqlite3.connect(str(config.graph_path))
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0].lower()
        conn.close()
        assert mode == "wal"

        root_path = config.sandbox_root

    # After exiting with block, root should be cleaned up
    assert not root_path.exists()


def test_state_delta_positive_mutations():
    """Verify StateDeltaAuditor accurately accounts for positive SQLite mutations."""
    with SandboxEnvironment() as config:
        auditor = StateDeltaAuditor(config.graph_path)
        baseline = auditor.capture_snapshot()
        assert baseline.table_counts["node"] == 0

        # Execute mutations
        conn = sqlite3.connect(str(config.graph_path))
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO node (node_id, node_type, name, created_at) VALUES (?, ?, ?, ?)",
            ("node:system-1", "system", "EMS Substation", "2026-09-13T00:00:00Z"),
        )
        cur.execute(
            "INSERT INTO node (node_id, node_type, name, created_at) VALUES (?, ?, ?, ?)",
            ("node:system-2", "system", "SCADA Relay", "2026-09-13T00:00:00Z"),
        )
        cur.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_value, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ast:1", "node:system-1", "monitors", "node:system-2", "CONFIRMED", "2026-09-13T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        report = auditor.compute_delta(baseline)
        assert not report.is_idempotent_noop
        assert report.hash_changed
        assert report.table_deltas["node"] == 2
        assert report.table_deltas["assertion"] == 1
        assert report.total_mutations == 3


def test_state_delta_idempotent_noop():
    """Verify StateDeltaAuditor certifies idempotent zero-op executions."""
    with SandboxEnvironment() as config:
        auditor = StateDeltaAuditor(config.graph_path)
        baseline = auditor.capture_snapshot()

        # Execute zero mutations (noop)
        report = auditor.compute_delta(baseline)
        assert report.is_idempotent_noop
        assert not report.hash_changed
        assert report.total_mutations == 0
        assert all(delta == 0 for delta in report.table_deltas.values())


def test_chaos_injector_transient_rate_limit_recovery():
    """Verify ChaosInjector simulates transient 429 rate limit before recovering."""
    injector = ChaosInjector()

    def dummy_fetch_data(query: str) -> str:
        return f"Results for {query}"

    # Configure 2 consecutive rate-limit faults before recovery
    resilient_tool = injector.wrap_callable(
        dummy_fetch_data, failure_mode="rate_limit", trigger_count=2
    )

    # Call 1: fails with ChaosSimulationError
    with pytest.raises(ChaosSimulationError) as exc_info:
        resilient_tool("test_q")
    assert "HTTP 429" in str(exc_info.value)

    # Call 2: fails with ChaosSimulationError
    with pytest.raises(ChaosSimulationError) as exc_info:
        resilient_tool("test_q")
    assert "HTTP 429" in str(exc_info.value)

    # Call 3: recovers and returns nominal result
    res = resilient_tool("test_q")
    assert res == "Results for test_q"

    assert injector.metrics.total_calls == 3
    assert injector.metrics.injected_faults == 2
    assert injector.metrics.recovered_calls == 1


def test_chaos_injector_timeout_and_truncated_payload():
    """Verify ChaosInjector simulates timeouts and truncated payloads."""
    injector = ChaosInjector()

    def target_call() -> str:
        return '{"status": "complete", "records": [1, 2, 3]}'

    # Test timeout
    timeout_fn = injector.wrap_callable(target_call, failure_mode="timeout", trigger_count=1)
    with pytest.raises(TimeoutError):
        timeout_fn()

    # Test truncated payload
    truncated_fn = injector.wrap_callable(target_call, failure_mode="truncated_payload", trigger_count=1)
    truncated_output = truncated_fn()
    assert truncated_output == '{"status": "incomp'
    # Second call should recover and return nominal
    recovered_output = truncated_fn()
    assert '"status": "complete"' in recovered_output
