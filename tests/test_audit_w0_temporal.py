"""Unit tests for W0 Temporal Grounding Audit and Graph Freshness (Wave 4)."""

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
def temp_graph_db(tmp_path: Path):
    db_path = tmp_path / "test_graph.sqlite"
    conn = sqlite3.connect(db_path)
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
    conn.commit()
    conn.close()
    return db_path


def test_w0_temporal_valid_assertions_pass(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('a1', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-01', '2026-10-01')"
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('a2', 'n2', 'PRED', 'CONFIRMED', 'current', '09/15/2026', '12/31/2026')"
    )
    conn.commit()
    conn.close()

    res = validate_assertion_temporal_grounding(temp_graph_db, reference_date=date(2026, 9, 30))
    assert res["status"] == "PASS"
    assert res["total_checked"] == 2
    assert res["valid_count"] == 2
    assert res["violation_count"] == 0


def test_w0_temporal_missing_observed_at_raises(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('a_missing', 'n1', 'PRED', 'CONFIRMED', 'current', NULL, '2026-10-01')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(TemporalGroundingError) as exc_info:
        validate_assertion_temporal_grounding(temp_graph_db, reference_date=date(2026, 9, 30))
    assert "MISSING_OBSERVED_AT" in str(exc_info.value)


def test_w0_temporal_future_observed_at_raises(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('a_future', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-10-15', '2026-11-01')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(TemporalGroundingError) as exc_info:
        validate_assertion_temporal_grounding(temp_graph_db, reference_date=date(2026, 9, 30))
    assert "FUTURE_OBSERVED_AT" in str(exc_info.value)


def test_w0_temporal_inverted_interval_raises(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('a_inverted', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-20', '2026-09-10')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(TemporalGroundingError) as exc_info:
        validate_assertion_temporal_grounding(temp_graph_db, reference_date=date(2026, 9, 30))
    assert "INVERTED_TEMPORAL_INTERVAL" in str(exc_info.value)


def test_w0_temporal_fail_closed_false_returns_report(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('a_bad', 'n1', 'PRED', 'CONFIRMED', 'current', '2026-09-20', '2026-09-10')"
    )
    conn.commit()
    conn.close()

    report = validate_assertion_temporal_grounding(
        temp_graph_db, reference_date=date(2026, 9, 30), fail_closed=False
    )
    assert report["status"] == "VIOLATION"
    assert report["violation_count"] == 1


def test_freshness_all_current_pass(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('a1', 'n1', 'P', 'CONFIRMED', 'current', '2026-09-01', '2026-10-15')"
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('a2', 'n2', 'P', 'CONFIRMED', 'current', '2026-09-02', '2026-10-20')"
    )
    conn.commit()
    conn.close()

    res = calculate_graph_freshness(temp_graph_db, as_of_date=date(2026, 9, 30))
    assert res["status"] == "PASS"
    assert res["freshness_ratio"] == 1.0
    assert res["active_stale"] == 0


def test_freshness_alert_threshold(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    # 8 fresh, 2 stale -> ratio = 0.80 (< 0.90 alert, >= 0.70 pass)
    for i in range(8):
        conn.execute(
            f"INSERT INTO assertion VALUES ('f_{i}', 'n', 'P', 'CONFIRMED', 'current', '2026-09-01', '2026-10-15')"
        )
    for i in range(2):
        conn.execute(
            f"INSERT INTO assertion VALUES ('s_{i}', 'n', 'P', 'CONFIRMED', 'current', '2026-09-01', '2026-09-15')"
        )
    conn.commit()
    conn.close()

    res = calculate_graph_freshness(temp_graph_db, as_of_date=date(2026, 9, 30))
    assert res["status"] == "ALERT"
    assert res["alert_triggered"] is True
    assert res["freshness_ratio"] == 0.80


def test_freshness_fail_threshold(temp_graph_db):
    conn = sqlite3.connect(temp_graph_db)
    # 5 fresh, 5 stale -> ratio = 0.50 (< 0.70 failed)
    for i in range(5):
        conn.execute(
            f"INSERT INTO assertion VALUES ('f_{i}', 'n', 'P', 'CONFIRMED', 'current', '2026-09-01', '2026-10-15')"
        )
    for i in range(5):
        conn.execute(
            f"INSERT INTO assertion VALUES ('s_{i}', 'n', 'P', 'CONFIRMED', 'current', '2026-09-01', '2026-09-15')"
        )
    conn.commit()
    conn.close()

    res = calculate_graph_freshness(temp_graph_db, as_of_date=date(2026, 9, 30), fail_closed=False)
    assert res["status"] == "FAILED"
    assert res["fail_closed_triggered"] is True
    assert res["freshness_ratio"] == 0.50

    with pytest.raises(GraphFreshnessError):
        calculate_graph_freshness(temp_graph_db, as_of_date=date(2026, 9, 30), fail_closed=True)
