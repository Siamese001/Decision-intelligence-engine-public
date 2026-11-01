"""End-to-End test for Structural Monotonicity Guard and MetricTrendAnalyzer.

Lifecycle:
1. Baseline graph state initialized.
2. Additive mutation verified through monotonicity guard.
3. Trend metrics captured across sequential generations.
4. Unauthorized silent deletion intercepted and blocked with StructuralMonotonicityError.
5. Explicitly authorized deletion permitted with audit trail.
6. Multi-generation gradual degradation detected via MetricTrendAnalyzer (drift + consecutive decline).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.graph_delta import (
    MetricTrendAnalyzer,
    StructuralMonotonicityError,
    verify_structural_monotonicity,
)


@pytest.fixture
def mock_graph_workspace(tmp_path: Path):
    graph_path = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(graph_path)
    conn.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE source_version (version_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT)")
    conn.execute("CREATE TABLE node_state (node_id TEXT PRIMARY KEY, approval_state TEXT)")
    conn.execute(
        "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, canonical_status TEXT, currentness_state TEXT, superseded_at TEXT)"
    )
    conn.execute("CREATE TABLE search_document (doc_id TEXT PRIMARY KEY, canonical_status TEXT)")
    conn.execute("CREATE TABLE audit_event (event_id TEXT PRIMARY KEY)")

    # Baseline seed data
    conn.execute("INSERT INTO source VALUES ('src:01')")
    conn.execute("INSERT INTO source_version VALUES ('ver:01')")
    for i in range(10):
        conn.execute(f"INSERT INTO evidence_item VALUES ('ev:{i:02d}')")
        conn.execute(f"INSERT INTO node VALUES ('node:{i:02d}', 'entity')")
        conn.execute(f"INSERT INTO node_state VALUES ('node:{i:02d}', 'candidate')")
        conn.execute(f"INSERT INTO assertion VALUES ('as:{i:02d}', 'canonical', 'current', NULL)")
        conn.execute(f"INSERT INTO search_document VALUES ('doc:{i:02d}', 'canonical')")
    conn.commit()
    conn.close()
    return graph_path


def _get_counts(graph_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(graph_path)
    counts = {
        "source": conn.execute("SELECT count(*) FROM source").fetchone()[0],
        "source_version": conn.execute("SELECT count(*) FROM source_version").fetchone()[0],
        "evidence": conn.execute("SELECT count(*) FROM evidence_item").fetchone()[0],
        "internal_entity": conn.execute("SELECT count(*) FROM node").fetchone()[0],
        "canonical_assertion": conn.execute(
            "SELECT count(*) FROM assertion WHERE canonical_status='canonical'"
        ).fetchone()[0],
        "search_document": conn.execute("SELECT count(*) FROM search_document").fetchone()[0],
    }
    conn.close()
    return counts


def test_structural_monotonicity_and_trend_lifecycle_e2e(mock_graph_workspace):
    graph_path = mock_graph_workspace
    trend = MetricTrendAnalyzer(min_history_for_stats=3)

    # 1. Baseline
    c0 = _get_counts(graph_path)
    trend.record_generation("gen-00", {"canonical_assertions": c0["canonical_assertion"]})

    # 2. Additive mutation (Generation 1)
    conn = sqlite3.connect(graph_path)
    conn.execute("INSERT INTO assertion VALUES ('as:10', 'canonical', 'current', NULL)")
    conn.execute("INSERT INTO search_document VALUES ('doc:10', 'canonical')")
    conn.commit()
    conn.close()

    c1 = _get_counts(graph_path)
    res1 = verify_structural_monotonicity(c0, c1)
    assert res1["status"] == "PASS"
    assert res1["is_monotonic"] is True
    assert res1["deltas"]["canonical_assertion"] == 1
    trend.record_generation("gen-01", {"canonical_assertions": c1["canonical_assertion"]})

    # 3. Additive mutation (Generation 2)
    conn = sqlite3.connect(graph_path)
    conn.execute("INSERT INTO assertion VALUES ('as:11', 'canonical', 'current', NULL)")
    conn.commit()
    conn.close()

    c2 = _get_counts(graph_path)
    res2 = verify_structural_monotonicity(c1, c2)
    assert res2["status"] == "PASS"
    trend.record_generation("gen-02", {"canonical_assertions": c2["canonical_assertion"]})

    # 4. Attempt unauthorized silent deletion (drop 3 assertions)
    conn = sqlite3.connect(graph_path)
    conn.execute("DELETE FROM assertion WHERE assertion_id IN ('as:00', 'as:01', 'as:02')")
    conn.commit()
    conn.close()

    c3_bad = _get_counts(graph_path)
    with pytest.raises(StructuralMonotonicityError) as exc_info:
        verify_structural_monotonicity(c2, c3_bad, allow_deletions=False)
    assert "canonical_assertion (12 -> 9, delta=-3)" in str(exc_info.value)

    # 5. Authorize explicit deletion
    res3_authorized = verify_structural_monotonicity(
        c2, c3_bad, allow_deletions={"canonical_assertion"}
    )
    assert res3_authorized["status"] == "PASS"
    assert res3_authorized["deltas"]["canonical_assertion"] == -3
    trend.record_generation("gen-03", {"canonical_assertions": c3_bad["canonical_assertion"]})

    # 6. Simulate further consecutive declines to trigger trend degradation alert
    trend.record_generation("gen-04", {"canonical_assertions": 8})
    trend.record_generation("gen-05", {"canonical_assertions": 7})

    analysis = trend.analyze_metric_drift("canonical_assertions")
    assert analysis["status"] == "ALERT"
    assert analysis["consecutive_declines_detected"] is True
    assert analysis["consecutive_declines"] >= 3
