"""Adversarial tests for W0 temporal grounding audit and graph freshness gating."""

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
def adversarial_graph_db(tmp_path: Path):
    db_path = tmp_path / "adv_graph.sqlite"
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


def test_adversarial_malformed_timestamp_strings(adversarial_graph_db):
    conn = sqlite3.connect(adversarial_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('adv_1', 'n1', 'P', 'CONFIRMED', 'current', 'not-a-date', '2026-10-01')"
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('adv_2', 'n2', 'P', 'CONFIRMED', 'current', '2026-09-01', 'random-gibberish')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(TemporalGroundingError) as exc_info:
        validate_assertion_temporal_grounding(
            adversarial_graph_db, reference_date=date(2026, 9, 30)
        )
    err = str(exc_info.value)
    assert "MALFORMED_TIMESTAMP" in err


def test_adversarial_sql_injection_attempt_in_timestamp(adversarial_graph_db):
    conn = sqlite3.connect(adversarial_graph_db)
    injection_payload = "2026-09-01'; DROP TABLE assertion; --"
    conn.execute(
        "INSERT INTO assertion VALUES ('adv_inj', 'n1', 'P', 'CONFIRMED', 'current', ?, '2026-10-01')",
        (injection_payload,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(TemporalGroundingError) as exc_info:
        validate_assertion_temporal_grounding(
            adversarial_graph_db, reference_date=date(2026, 9, 30)
        )
    assert "MALFORMED_TIMESTAMP" in str(exc_info.value)


def test_adversarial_boundary_same_day_observed_and_stale(adversarial_graph_db):
    """When observed_at == stale_after, interval is technically valid (0 duration), not inverted."""
    conn = sqlite3.connect(adversarial_graph_db)
    conn.execute(
        "INSERT INTO assertion VALUES ('adv_same', 'n1', 'P', 'CONFIRMED', 'current', '2026-09-25', '2026-09-25')"
    )
    conn.commit()
    conn.close()

    res = validate_assertion_temporal_grounding(
        adversarial_graph_db, reference_date=date(2026, 9, 30)
    )
    assert res["status"] == "PASS"


def test_adversarial_empty_assertion_table_freshness(adversarial_graph_db):
    """Zero assertions should gracefully evaluate to freshness 1.0 (no degradation)."""
    res = calculate_graph_freshness(adversarial_graph_db, as_of_date=date(2026, 9, 30))
    assert res["status"] == "PASS"
    assert res["freshness_ratio"] == 1.0
    assert res["total_active"] == 0


def test_adversarial_total_graph_staleness_collapse(adversarial_graph_db):
    """All active assertions are stale -> 0.0 freshness ratio fails closed immediately."""
    conn = sqlite3.connect(adversarial_graph_db)
    for i in range(10):
        conn.execute(
            f"INSERT INTO assertion VALUES ('stale_{i}', 'n', 'P', 'CONFIRMED', 'current', '2025-01-01', '2025-02-01')"
        )
    conn.commit()
    conn.close()

    with pytest.raises(GraphFreshnessError) as exc_info:
        calculate_graph_freshness(
            adversarial_graph_db, as_of_date=date(2026, 9, 30), fail_closed=True
        )
    assert "below fail threshold" in str(exc_info.value)
