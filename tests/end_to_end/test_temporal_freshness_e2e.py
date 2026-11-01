"""End-to-End test for Temporal Grounding Audit and Freshness Ratio Lifecycle.

Lifecycle:
1. Valid initial graph mutation with compliant ISO-8601 temporal attributes.
2. W0 temporal audit passes with 100% compliant grounding.
3. Graph freshness ratio calculated as 1.0 (PASS).
4. Time advancement causes partial assertion staleness (ALERT threshold triggered).
5. Further passage of time causes comprehensive staleness (FAIL threshold triggered).
6. Malformed and inverted temporal intervals intercepted and blocked closed.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from dual_pipeline.audit_w0 import (
    TemporalGroundingError,
    validate_assertion_temporal_grounding,
)
from dual_pipeline.graph_signal_core.freshness import (
    GraphFreshnessError,
    calculate_graph_freshness,
)


@pytest.fixture
def e2e_temporal_graph(tmp_path: Path):
    graph_path = tmp_path / "e2e_temporal.sqlite"
    conn = sqlite3.connect(graph_path)
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            status TEXT DEFAULT 'CONFIRMED',
            currentness_state TEXT DEFAULT 'current',
            observed_at TEXT,
            stale_after TEXT
        )"""
    )

    # Initial batch: 10 assertions observed 2026-09-01, stale_after 2026-10-15
    for i in range(10):
        conn.execute(
            f"INSERT INTO assertion VALUES ('as:{i:02d}', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-01', '2026-10-15')"
        )
    conn.commit()
    conn.close()
    return graph_path


def test_temporal_freshness_lifecycle_e2e(e2e_temporal_graph):
    graph_path = e2e_temporal_graph
    ref_date = date(2026, 9, 30)

    # Step 1: Initial W0 audit check
    w0_report = validate_assertion_temporal_grounding(graph_path, reference_date=ref_date)
    assert w0_report["status"] == "PASS"
    assert w0_report["total_checked"] == 10
    assert w0_report["violation_count"] == 0

    # Step 2: Initial Freshness check (100% active, 0 stale)
    f_initial = calculate_graph_freshness(graph_path, as_of_date=ref_date)
    assert f_initial["status"] == "PASS"
    assert f_initial["freshness_ratio"] == 1.0

    # Step 3: Advance to 2026-10-10 and add 2 assertions that expired on 2026-10-05
    conn = sqlite3.connect(graph_path)
    conn.execute(
        "INSERT INTO assertion VALUES ('as:expired_1', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-01', '2026-10-05')"
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('as:expired_2', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-01', '2026-10-05')"
    )
    conn.commit()
    conn.close()

    # Total = 12, Stale = 2 -> Freshness = 10/12 = 0.8333 (< 0.90 -> ALERT)
    f_alert = calculate_graph_freshness(graph_path, as_of_date=date(2026, 10, 10))
    assert f_alert["status"] == "ALERT"
    assert f_alert["alert_triggered"] is True
    assert 0.80 <= f_alert["freshness_ratio"] < 0.90

    # Step 4: Advance to 2026-10-20 -> all 12 assertions are now past stale_after!
    # Freshness = 0/12 = 0.0 (< 0.70 -> FAILED)
    f_failed = calculate_graph_freshness(
        graph_path, as_of_date=date(2026, 10, 20), fail_closed=False
    )
    assert f_failed["status"] == "FAILED"
    assert f_failed["fail_closed_triggered"] is True

    with pytest.raises(GraphFreshnessError):
        calculate_graph_freshness(graph_path, as_of_date=date(2026, 10, 20), fail_closed=True)

    # Step 5: Adversarial injection of inverted temporal interval
    conn = sqlite3.connect(graph_path)
    conn.execute(
        "INSERT INTO assertion VALUES ('as:bad_invert', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-20', '2026-08-15')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(TemporalGroundingError) as exc_info:
        validate_assertion_temporal_grounding(graph_path, reference_date=ref_date)
    assert "INVERTED_TEMPORAL_INTERVAL" in str(exc_info.value)
