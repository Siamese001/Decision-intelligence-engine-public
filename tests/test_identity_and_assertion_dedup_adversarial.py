"""Wave 6 adversarial and edge-case tests for identity resolution, assertion deduplication, and delta counting."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import connect_sqlite, digest, sha256_file, utc_now
from dual_pipeline.graph_delta_core.counts import _graph_counts, _planned_increments
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from dual_pipeline.identity_core.matching import (
    are_node_types_compatible,
    determine_assessment_status,
    determine_resolution_outcome,
)
from dual_pipeline.intake_core.comparison import _find_lexical_matches
from dual_pipeline.intake_engine_core.disposition_flow import _is_operation_payload_valid
from tests.end_to_end.test_graph_deduplication_and_reingestion_e2e import _seed_test_graph


def test_boundary_and_malformed_inputs_in_compatibility_and_lexical_match() -> None:
    """Category 1 (Boundary payload) & Category 2 (Malformed input): empty strings, None types, title/body doubling."""
    assert are_node_types_compatible(None, None) is True
    assert are_node_types_compatible("", "workstream") is True

    text = "conduit platform enforces deterministic schema validation across all stages"
    admitted = [
        {
            "search_document_id": "sd-001",
            "object_id": "asrt-001",
            "title": "Conduit Platform enforces deterministic schema validation across all stages.",
            "body": "Conduit Platform enforces deterministic schema validation across all stages.",
            "canonical_status": "candidate",
            "retrieval_eligible": 0,
            "source_id": "src-001",
        }
    ]
    exact, partial, _ = _find_lexical_matches(text, admitted)
    assert len(exact) == 1
    assert len(partial) == 0


def test_high_confidence_auto_link_and_ambiguity_margin_gate() -> None:
    """Category 4 (Overflow/threshold boundary) & Category 5 (Invariant bypass): AUTO_LINK_HIGH_CONFIDENCE requires score>=0.90, margin>=0.25, supporting_context, and no conflicts."""
    outcome, _ = determine_resolution_outcome(
        type_conflict=False,
        exact_matches=[],
        profile={"entity_intent": "resolve_or_create", "mutation_intent": "UPDATE"},
        declared_type_valid=True,
        stable_identifier="workstream:conduit",
        alternatives=[
            {
                "entity_id": "workstream_conduit",
                "canonical_name": "Conduit Workstream",
                "match_score": 0.94,
                "margin_over_next": 0.35,
                "supporting_context": ["Context overlap: conduit, workstream"],
                "identity_conflicts": [],
            }
        ],
    )
    assert outcome == "AUTO_LINK_HIGH_CONFIDENCE"
    assert determine_assessment_status(
        outcome=outcome,
        type_conflict=False,
        exact_count=0,
        profile={"entity_intent": "resolve_or_create"},
        declared_type_valid=True,
    ) == "EXACT_EXISTING_LINK"

    outcome_no_ctx, _ = determine_resolution_outcome(
        type_conflict=False,
        exact_matches=[],
        profile={"entity_intent": "unknown"},
        declared_type_valid=False,
        stable_identifier=None,
        alternatives=[
            {
                "entity_id": "workstream_conduit",
                "canonical_name": "Conduit Workstream",
                "match_score": 0.95,
                "margin_over_next": 0.40,
                "supporting_context": [],
                "identity_conflicts": [],
            }
        ],
    )
    assert outcome_no_ctx == "HITL_CONFIRM_ENTITY"


def test_operation_payload_lane_validation_for_relationship_action_and_evidence() -> None:
    """Category 5 (Invariant bypass) & Category 7 (Production regression): _is_operation_payload_valid allows APPROVED and CANDIDATE on add_relationship, add_action_item, add_evidence."""
    for op_name in ("add_relationship", "add_action_item", "add_evidence"):
        assert _is_operation_payload_valid({"operation": op_name}, {"disposition": "CANDIDATE"}) is True
        assert _is_operation_payload_valid({"operation": op_name}, {"disposition": "APPROVED"}) is True
        assert _is_operation_payload_valid({"operation": op_name}, {"disposition": "QUARANTINE"}) is False


def test_candidate_to_canonical_fingerprint_upgrade_delta_accounting(tmp_path: Path) -> None:
    """Category 6 (Rollback & recovery / idempotent upgrade) & Category 7 (Production regression): upgrading an existing candidate assertion to APPROVED accurately decrements candidate_assertion and increments canonical_assertion."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_test_graph(graph_path)

    conn = connect_sqlite(graph_path)
    try:
        subject_id = conn.execute("SELECT node_id FROM node WHERE active=1 LIMIT 1").fetchone()[0]
        conn.execute(
            """INSERT OR REPLACE INTO governance_approval
               (approval_id, approval_reference, decision_scope, status, approved_by, approved_at, scope_json, active)
               VALUES (?, ?, 'release', 'approved', 'operator', ?, ?, 1)""",
            (
                "gov-app-upgrade-001",
                "AD-UPGRADE-001",
                utc_now(),
                json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}),
            ),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    cand_dict = {
        "candidate_id": "cand-upg-001",
        "candidate_type": "assertion",
        "assertion_type": "decision",
        "normalized_text": "All graph mutations require five-check physical proof verification.",
        "materiality": "high",
        "entity_mentions": [],
        "evidence_ids": ["ev-upg-001"],
        "source_locator": {"kind": "timestamp", "value": "00:01:00"},
        "classification": "internal",
        "audiences": ["internal_delivery"],
        "extraction_confidence": 0.92,
    }
    source_block = {
        "source_id": "src-upg-001",
        "source_version_id": "sv-upg-001",
        "name": "Upgrade Test Meeting",
        "date": "2026-10-25",
        "classification": "internal",
        "audiences": ["internal_delivery"],
        "authority_rank": 8,
        "evidence": [
            {
                "evidence_id": "ev-upg-001",
                "source_id": "src-upg-001",
                "source_version_id": "sv-upg-001",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
                "directness": "verbatim_quote",
                "authority_rank": 8,
                "content_digest": digest("All graph mutations require five-check physical proof verification."),
                "direct_quote": "All graph mutations require five-check physical proof verification.",
                "classification": "internal",
                "audiences": ["internal_delivery"],
                "extraction_confidence": 0.92,
            }
        ],
    }

    payload_cand = {
        "candidate": cand_dict,
        "disposition": "CANDIDATE",
        "subject_node_id": subject_id,
        "object_node_id": None,
        "predicate": "HAS_CLAIM_TEXT",
        "action_status": None,
    }
    cs_cand = create_artifact(
        "graph-change-set",
        "cs-upg-cand",
        change_set_id="cs-upg-cand",
        run_id="run-upg-1",
        trace_id="1" * 32,
        base_graph_snapshot="snap-1",
        base_graph_hash=sha256_file(graph_path),
        status="staged",
        approval_id="gov-app-upgrade-001",
        transaction_required=True,
        rollback_required=True,
        operations=[
            {
                "operation_id": "op-upg-cand",
                "sequence": 1,
                "operation": "add_candidate_assertion",
                "target_type": "assertion",
                "target_id": "asrt-upg-cand-001",
                "before_state_hash": "0" * 64,
                "after_state_hash": digest(payload_cand),
                "evidence_ids": ["ev-upg-001"],
                "classification": "internal",
                "audiences": ["internal_delivery"],
                "candidate_id": "cand-upg-001",
            }
        ],
        extensions={
            "com.partner_advisory.nee.source": source_block,
            "com.partner_advisory.nee.operation-payloads": {"op-upg-cand": payload_cand},
        },
    )
    writer = GovernedGraphWriter(graph_path)
    writer.apply(cs_cand)

    payload_app = {**payload_cand, "disposition": "APPROVED"}
    cs_app = create_artifact(
        "graph-change-set",
        "cs-upg-app",
        change_set_id="cs-upg-app",
        run_id="run-upg-2",
        trace_id="2" * 32,
        base_graph_snapshot="snap-2",
        base_graph_hash=sha256_file(graph_path),
        status="approved",
        approval_id="gov-app-upgrade-001",
        transaction_required=True,
        rollback_required=True,
        operations=[
            {
                "operation_id": "op-upg-app",
                "sequence": 1,
                "operation": "add_approved_assertion",
                "target_type": "assertion",
                "target_id": "asrt-upg-app-002",
                "before_state_hash": "0" * 64,
                "after_state_hash": digest(payload_app),
                "evidence_ids": ["ev-upg-001"],
                "classification": "internal",
                "audiences": ["internal_delivery"],
                "candidate_id": "cand-upg-001",
            }
        ],
        extensions={
            "com.partner_advisory.nee.source": source_block,
            "com.partner_advisory.nee.operation-payloads": {"op-upg-app": payload_app},
        },
    )
    before_counts = _graph_counts(graph_path)
    planned_inc = _planned_increments(cs_app, graph_path)
    assert planned_inc["candidate_assertion"] == -1
    assert planned_inc["canonical_assertion"] == 1
    assert planned_inc["candidate_search_document"] == -1

    writer.apply(cs_app)
    after_counts = _graph_counts(graph_path)
    for k, inc in planned_inc.items():
        assert after_counts[k] - before_counts[k] == inc
