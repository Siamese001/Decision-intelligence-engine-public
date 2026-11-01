"""
tests/end_to_end/test_hybrid_retrieval_coverage_e2e.py

End-to-end verification of Wave 3: Retrieval Clearance, Candidate Lane Eligibility &
Sidecar Re-Indexing across sandbox clones of the active GraphDB, render_retrieval_records,
classify_retrieval_eligibility, and exact_retrieval (search_fts BM25).
"""

from __future__ import annotations

import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from dual_pipeline.index import render_retrieval_records
from dual_pipeline.index_core.eligibility import (
    classify_retrieval_eligibility,
    effective_retrieval_lane,
)
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.runtime_root import resolve_active_graph_path, resolve_authoritative_runtime_root
from storage.connection import create_sqlite_conn


def test_e2e_active_graphdb_sandbox_retrieval_coverage_and_candidate_bm25(tmp_path: Path) -> None:
    """
    Verifies Wave 3 acceptance criteria on an ephemeral sandbox clone of the active GraphDB:
    1. EXCLUDED_CLEARANCE_CONFIDENTIAL_RECORD_EXPOSED_TO_CLIENT_AUDIENCE drops to 0 for internal nee-engineering records.
    2. Sidecar-eligible current assertions increase from 1,054 (42.7%) to >2,300 (>93%).
    3. search_fts BM25 lexical queries return matching candidate lane assertions alongside approved canonical assertions.
    """
    rt_root = resolve_authoritative_runtime_root()
    active_graph = resolve_active_graph_path(rt_root, enforce_integrity=False)
    if not active_graph.is_file():
        pytest.skip("Active GraphDB not provisioned in local environment")

    sandbox_graph = tmp_path / "sandbox_graph.sqlite"
    shutil.copy2(active_graph, sandbox_graph)

    # Rebuild search_fts in the sandbox so candidate search_document rows are indexed in FTS5
    conn = create_sqlite_conn(sandbox_graph, readonly=False)
    conn.execute("INSERT INTO search_fts(search_fts) VALUES('rebuild')")
    conn.commit()

    # Inspect all current assertions and their eligibility classification
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.*, EXISTS (
            SELECT 1 FROM assertion_evidence ae
            WHERE ae.assertion_id = a.assertion_id
              AND ae.evidence_relationship = 'supports'
        ) AS has_supp_ev
        FROM assertion a
        WHERE a.currentness_state = 'current'
          AND (a.superseded_by_assertion_id IS NULL OR a.superseded_by_assertion_id = '')
        """
    )
    current_rows = cur.fetchall()
    conn.close()

    total_current = len(current_rows)
    assert total_current >= 2400

    reason_counts: Counter[str] = Counter()
    lane_counts: Counter[str] = Counter()
    for row in current_rows:
        row_dict = dict(row)
        el, reason = classify_retrieval_eligibility(
            row_dict,
            has_supporting_evidence=bool(row_dict["has_supp_ev"]),
            allow_unapproved_canonical_as_candidate=True,
        )
        reason_counts[reason] += 1
        if el:
            lane_counts[effective_retrieval_lane(row_dict)] += 1

    assert reason_counts["EXCLUDED_CLEARANCE_CONFIDENTIAL_RECORD_EXPOSED_TO_CLIENT_AUDIENCE"] == 0
    assert reason_counts["EXCLUDED_UNAPPROVED_CANONICAL"] == 0
    eligible_count = reason_counts["ELIGIBLE"]
    assert eligible_count > 2300, f"Expected >2,300 eligible current assertions, got {eligible_count}"
    assert (eligible_count / total_current) > 0.93

    # Verify render_retrieval_records produces >2,300 non-facet assertion records
    rendered = render_retrieval_records(sandbox_graph, "wave3-sandbox-snapshot")
    assertion_records = [r for r in rendered if r["record_type"] != "entity_facet"]
    facet_records = [r for r in rendered if r["record_type"] == "entity_facet"]
    assert len(assertion_records) > 2300
    assert len(facet_records) > 500

    # Verify search_fts BM25 returns both approved and candidate lane hits on LENS stakeholder queries
    exact = exact_retrieval(
        "Eduardo More Amit Ayer LENS Atlas Rule Specialist",
        sandbox_graph,
        classification="restricted",
        audiences=["internal_consulting", "nee-engineering"],
        retrieval_mode="candidate_review",
    )
    assert exact["bm25_executed"] is True
    assert len(exact["bm25_ranks_by_lane"]["approved"]) > 0
    assert len(exact["bm25_ranks_by_lane"]["candidate"]) > 0
    candidate_bm25_anchors = [a for a in exact["bm25_anchors"] if a["lane"] == "candidate"]
    assert len(candidate_bm25_anchors) > 0
