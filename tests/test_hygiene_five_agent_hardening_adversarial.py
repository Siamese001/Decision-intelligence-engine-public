"""Adversarial and edge-case tests for 5-Agent Graph Hygiene & HITL Learning Hardening."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.hygiene_agent import _audit_promotable_candidates
from dual_pipeline.graph_maintenance_core.hygiene_mutation_agents import (
    normalize_merge_finding_survivors,
)
from dual_pipeline.graph_scout_core.matching import _string_similarity, has_conflicting_qualifiers
from dual_pipeline.learning_engine_core.rollback import verify_zero_graph_mutation
from tools.apply_graph_hygiene_remediation import _remediate_candidate_promotions


def test_adversarial_candidate_promotion_blocks_leaked_and_uneligible_drafts(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): CLUSTER-06 promotion must never promote CLUSTER-01B leaked or fact_promotion_eligible=false drafts."""
    db_path = tmp_path / "promo_guard.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            currentness_state TEXT,
            validation_state TEXT,
            approval_reference TEXT,
            confidence_score REAL,
            qualifiers_json TEXT,
            updated_at TEXT
        )"""
    )
    conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT)")
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-collide', 'candidate', 0, 1, 'current', 'needs_review', NULL, 0.92, '{\"fact_promotion_eligible\": false}', '2026-10-01')"
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-collide')")
    conn.commit()

    pkg = {
        "findings": [
            {
                "proposed_fix": "demote_leaked_candidate_assertion",
                "entity_references": ["asrt-collide"],
                "raw_data": {"assertion_id": "asrt-collide"},
            },
            {
                "proposed_fix": "batch_promote_candidates",
                "entity_references": ["asrt-collide"],
                "raw_data": {"promotable_ids": ["asrt-collide"]},
            },
        ]
    }
    promoted = _remediate_candidate_promotions(conn, pkg, "2026-10-28T00:00:00Z")
    assert promoted == 0
    status = conn.execute("SELECT canonical_status FROM assertion WHERE assertion_id = 'asrt-collide'").fetchone()[0]
    assert status == "candidate"
    conn.close()



def test_adversarial_boundary_empty_and_null_inputs(tmp_path: Path) -> None:
    """Category 1 (Boundary payload): Empty findings, empty strings, and 0-row candidate tables."""
    normalize_merge_finding_survivors(None, [])
    assert _string_similarity("", "") == 0.0
    assert has_conflicting_qualifiers("", "") is False

    db_path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE assertion (assertion_id TEXT, canonical_status TEXT)")
    cur = conn.cursor()
    assert _audit_promotable_candidates(cur, {"assertion_id", "canonical_status"}, excluded_ids=set()) is None
    conn.close()


def test_adversarial_malformed_merge_payloads() -> None:
    """Category 2 (Malformed input): Missing keys, self-loops, and None raw_data in merge_entities findings."""
    malformed_findings = [
        {"proposed_fix": "merge_entities", "raw_data": None},
        {"proposed_fix": "merge_entities", "raw_data": {"node_id_a": "same-id", "node_id_b": "same-id"}},
        {"proposed_fix": "merge_entities", "raw_data": {"node_id_a": "", "node_id_b": "only-b"}},
        {"proposed_fix": "other_action", "raw_data": {"node_id_a": "a", "node_id_b": "b"}},
    ]
    normalize_merge_finding_survivors(None, malformed_findings)
    assert malformed_findings[1]["raw_data"]["node_id_a"] == "same-id"


def test_adversarial_invariant_bypass_governed_db_mutation_caught(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass attempt): Governed DB mutation is caught even when test_timings.sqlite also changes."""
    gov_path = str(tmp_path / "graph.sqlite")
    timings_path = str(tmp_path / "test_timings.sqlite")
    before = {gov_path: "aaa11111", timings_path: "ttt11111"}
    after = {gov_path: "bbb22222", timings_path: "ttt99999"}
    ok, mutations = verify_zero_graph_mutation(before, after)
    assert ok is False
    assert len(mutations) == 1
    assert "graph.sqlite" in mutations[0]
