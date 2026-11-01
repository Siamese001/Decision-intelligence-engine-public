"""End-to-end and adversarial tests for Wave 2: Factual Retrieval Lane Isolation & Auxiliary Query Filter Parity."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from dual_pipeline.canonical_pipeline.forward_horizon_synthesizer import (
    get_executive_horizon_portfolio,
)
from dual_pipeline.graph_retrieval_core.recursive_traversal import (
    _build_cte_query,
    _fetch_subgraph_edges,
)
from dual_pipeline.index_core.constants import RETRIEVAL_MODE_POLICY
from dual_pipeline.index_core.eligibility import (
    classify_retrieval_eligibility,
    effective_retrieval_lane,
    is_single_source_directional,
)
from dual_pipeline.l2 import query_retrieval_policy
from dual_pipeline.l2_policy import retrieval_lanes_for_mode
from dual_pipeline.query_core.coverage import filter_packet_for_l2
from dual_pipeline.query_core.offline_retriever import _extract_assertions
from dual_pipeline.query_core.reasoning import evidence_only_over_packet, reason_over_packet
from dual_pipeline.query_core.traversal_helper import (
    _build_resource_traversal_query,
    _find_associated_workstreams,
)


def test_factual_mode_exclusively_returns_approved_lane() -> None:
    """Exact production regression: factual mode must never include candidate lane."""
    assert retrieval_lanes_for_mode("factual") == ("approved",)
    assert set(RETRIEVAL_MODE_POLICY["factual"].keys()) == {"approved"}
    mode, allowed_lanes = query_retrieval_policy("current_state_corroboration")
    assert mode == "factual"
    assert list(allowed_lanes) == ["approved"]


def test_eligibility_rejects_unapproved_or_low_confidence_from_approved_lane() -> None:
    """Boundary & invariant bypass: canonical_eligible=0 or confidence_score < 0.65 cannot enter approved lane."""
    row_uneligible = {
        "canonical_status": "canonical",
        "canonical_eligible": 0,
        "validation_state": "approved",
        "currentness_state": "current",
        "retrieval_eligible": 1,
        "confidence_score": 0.90,
        "classification": "internal",
        "allowed_audiences_json": '["engineering"]',
    }
    assert effective_retrieval_lane(row_uneligible) == "candidate"
    allowed_strict, reason_strict = classify_retrieval_eligibility(
        row_uneligible,
        has_supporting_evidence=True,
        allow_unapproved_canonical_as_candidate=False,
    )
    assert allowed_strict is False
    assert reason_strict == "EXCLUDED_UNAPPROVED_CANONICAL"

    row_low_conf = {
        "canonical_status": "canonical",
        "canonical_eligible": 1,
        "validation_state": "approved",
        "currentness_state": "current",
        "retrieval_eligible": 1,
        "confidence_score": 0.55,
        "classification": "internal",
        "allowed_audiences_json": '["engineering"]',
    }
    assert effective_retrieval_lane(row_low_conf) == "candidate"

    row_valid = {
        "canonical_status": "canonical",
        "canonical_eligible": 1,
        "validation_state": "approved",
        "currentness_state": "current",
        "retrieval_eligible": 1,
        "confidence_score": 1.0,
    }
    assert effective_retrieval_lane(row_valid) == "approved"


def test_single_source_directional_detection() -> None:
    """Verify directional/P1 single-source assertions are flagged."""
    row_bryce = {
        "corroborating_source_count": 1,
        "validation_priority": "P1",
        "primary_source_authority_rank": 5,
    }
    assert is_single_source_directional(row_bryce) is True

    row_multi = {
        "corroborating_source_count": 3,
        "validation_priority": "P3",
        "primary_source_authority_rank": 1,
    }
    assert is_single_source_directional(row_multi) is False


def test_coverage_and_reasoning_zero_candidate_records_in_factual_mode() -> None:
    """Verify filter_packet_for_l2, reason_over_packet, and evidence_only_over_packet strip candidates in factual mode."""
    plan = {
        "query_type": "current_state_fact",
        "retrieval_mode": "factual",
        "allowed_lanes": ["approved"],
        "primary_entities": ["Apex Global Enterprises"],
    }
    packet = {
        "approved": [
            {
                "record": {
                    "record_id": "rec-approved-1",
                    "record_type": "assertion",
                    "lane": "approved",
                    "title": "PartnerAdvisory allocates 66 engineers to NEA",
                    "retrieval_text": "PartnerAdvisory allocates 66 engineers to NEA.",
                    "evidence_ids": ["ev-1"],
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            }
        ],
        "candidate": [
            {
                "record": {
                    "record_id": "rec-cand-1",
                    "record_type": "assertion",
                    "lane": "candidate",
                    "title": "FieldFlow AI Single Pane of Glass",
                    "retrieval_text": "FieldFlow AI acts as Single Pane of Glass.",
                    "evidence_ids": ["ev-cand-1"],
                },
                "retrieval_metadata": {"answer_use": "candidate_hypothesis"},
            }
        ],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "gaps": [],
    }
    report = {
        "l2_input_record_ids_by_lane": {
            "approved": ["rec-approved-1"],
            "candidate": ["rec-cand-1"],
            "external_public": [],
        },
        "required_material_record_ids_by_lane": {
            "approved": ["rec-approved-1"],
            "candidate": [],
            "external_public": [],
        },
        "requirement_outcomes": [
            {
                "requirement_id": "req-pol-1",
                "outcome": "excluded",
                "reason_code": "CANDIDATE_LANE_EXCLUDED_BY_POLICY",
                "record_ids": [],
            }
        ],
    }
    filtered = filter_packet_for_l2(packet, report)
    assert filtered["candidate"] == []

    ans_reasoned = reason_over_packet(plan, packet)
    assert ans_reasoned["hypotheses"] == []
    assert ans_reasoned["source_evidence"] == []

    ans_ev_only = evidence_only_over_packet(plan, packet, reason_code="L2_FORBIDDEN")
    assert ans_ev_only["hypotheses"] == []
    assert ans_ev_only["source_evidence"] == []


def test_auxiliary_query_modules_enforce_canonical_triple_predicate() -> None:
    """Verify recursive_traversal, offline_retriever, traversal_helper, and forward_horizon_synthesizer filter out candidates."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave2_") as tmp_dir:
        db_path = Path(tmp_dir) / "test_graph.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                description TEXT,
                is_quarantined INTEGER DEFAULT 0
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                superseded_by_assertion_id TEXT,
                confidence_label TEXT,
                materiality TEXT,
                is_quarantined INTEGER DEFAULT 0,
                observed_at TEXT
            );
            INSERT INTO node VALUES ('n_nee', 'org', 'Apex Global Enterprises', 'Apex Global Enterprises', '', 0);
            INSERT INTO node VALUES ('node_workstream_1', 'workstream', 'Workstream Alpha', 'Workstream Alpha', '', 0);
            INSERT INTO node VALUES ('n_person_1', 'stakeholder', 'Jane Doe', 'Jane Doe', 'Lead', 0);

            -- Canonical assertion
            INSERT INTO assertion VALUES (
                'asrt_can_1', 'n_nee', 'CONFIRMED_DECISION', 'CONFIRMED_DECISION', 'node_workstream_1',
                'Canonical Decision', 'Canonical Decision', 'canonical', 1, 'current', 1, NULL, 'CONFIRMED', 'high', 0, '2026-07-10'
            );
            -- Candidate assertion (must be excluded)
            INSERT INTO assertion VALUES (
                'asrt_cand_1', 'n_nee', 'HAS_RISK', 'HAS_RISK', 'n_person_1',
                'Unverified Candidate Risk', 'Unverified Candidate Risk', 'candidate', 0, 'current', 0, NULL, 'LOW', 'low', 0, '2026-07-10'
            );
            """
        )
        conn.commit()
        cur = conn.cursor()

        # 1. recursive_traversal
        cte_sql, cte_params = _build_cte_query(["n_nee"], 2, "both", None, use_assertions=True)
        assert "e.canonical_status = 'canonical'" in cte_sql
        assert "e.canonical_eligible = 1" in cte_sql
        traversed = [r[0] for r in cur.execute(cte_sql, cte_params).fetchall()]
        assert "node_workstream_1" in traversed
        assert "n_person_1" not in traversed

        edges = _fetch_subgraph_edges(cur, ["n_nee", "node_workstream_1", "n_person_1"], use_assertions=True)
        assert len(edges) == 1
        assert edges[0]["edge_id"] == "asrt_can_1"

        # 2. offline_retriever
        decisions, risks = _extract_assertions(cur, ["n_nee"], 10)
        assert len(decisions) == 1
        assert decisions[0].assertion_id == "asrt_can_1"
        assert len(risks) == 0

        # 3. traversal_helper
        res_sql = _build_resource_traversal_query(cur, "?", False, {"assertion"})
        assert res_sql is not None
        assert "a.canonical_status = 'canonical'" in res_sql
        assert "a.canonical_eligible = 1" in res_sql
        ws_assoc = _find_associated_workstreams(cur, "n_nee", True, False)
        assert ws_assoc == ("node_workstream_1",)

        # 4. forward_horizon_synthesizer
        fh_res = get_executive_horizon_portfolio("n_nee", conn)
        fact_ids = [f["id"] for f in fh_res["grounded_facts"]]
        assert fact_ids == ["asrt_can_1"]
        conn.close()
