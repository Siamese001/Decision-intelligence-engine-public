"""Unit tests for tone_analysis_core.analyzer, shadow_eval, and query_l1_shadow_core.builders."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from dual_pipeline.core import seal_artifact
from dual_pipeline.index_core.constants import SIDECAR_SCHEMA
from dual_pipeline.index_core.shadow_eval import (
    compute_index_b_margin_tie_rates,
    verify_edge_fact_reachability,
)
from dual_pipeline.l1_control import BudgetState
from dual_pipeline.query_l1_shadow_core.builders import (
    build_attempt_zero,
    build_observation_zero,
    build_off_shadow_comparison,
    build_shadow_terminal,
)
from dual_pipeline.query_l1_shadow_core.models import ShadowReceiptError
from dual_pipeline.tone_analysis_core.analyzer import (
    ParalinguisticToneAnalyzer,
    ToneCategory,
)


def test_paralinguistic_tone_analyzer_turns_and_formatting() -> None:
    analyzer = ParalinguisticToneAnalyzer()
    turns = [
        {"speaker": "Alice", "start": 0.0, "end": 4.0, "text": "We will approved and confirmed the final call today."},
        {"speaker": "Bob", "start": 5.0, "end": 6.0, "text": "Ok."},
        {"speaker": "Carol", "start": 7.0, "end": 12.0, "text": "I have a major concern and blocker with this approach."},
        {"speaker": "Alice", "start": 13.0, "end": 18.0, "text": "Looking back and reflecting on lessons learned together."},
    ]
    arc = analyzer.analyze_turns(turns)
    assert arc.decisiveness_count >= 1
    assert arc.tension_count >= 1
    assert arc.reflection_count >= 1
    assert len(arc.turn_telemetry) == 4
    assert arc.turn_telemetry[1].primary_tone == ToneCategory.DISENGAGED

    out2_md = analyzer.format_output_2_tone_arc(arc)
    assert "Meeting Tone" in out2_md
    out3_sig = analyzer.format_output_3_tone_signals(arc)
    assert isinstance(out3_sig, list) and len(out3_sig) >= 1

    parsed_turns = analyzer.parse_turns_from_text(
        "[00:01:00] **Alice**: Great idea, working together on this.\n[00:01:20] **Bob**: Agreed to move forward."
    )
    assert len(parsed_turns) >= 2


def test_shadow_eval_margin_tie_rates_and_reachability(tmp_path: Path) -> None:
    sidecar_b = tmp_path / "sidecar_b.sqlite"
    conn = sqlite3.connect(str(sidecar_b))
    conn.executescript(SIDECAR_SCHEMA)
    v1 = np.zeros(1024, dtype=np.float32)
    v1[0] = 1.0
    v2 = np.zeros(1024, dtype=np.float32)
    v2[1] = 1.0
    rec1 = {
        "record_id": "prop-1",
        "lane": "approved",
        "record_type": "proposition_unit",
        "namespace": "canonical",
        "retrieval_text_hash": "h1",
        "retrieval_kind": "assertion",
        "epistemic_status": "approved",
        "provenance_kind": "source_bound",
        "answer_use": "factual_answer",
        "extensions": {"com.partner_advisory.nee.graph-object-id": "a1"},
    }
    rec2 = {**rec1, "record_id": "prop-2", "retrieval_text_hash": "h2"}
    for r, vec in ((rec1, v1), (rec2, v2)):
        conn.execute(
            "INSERT INTO retrieval_record VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                r["record_id"],
                r["lane"],
                r["record_type"],
                r["namespace"],
                r["retrieval_text_hash"],
                r["retrieval_kind"],
                r["epistemic_status"],
                r["provenance_kind"],
                r["answer_use"],
                json.dumps(r),
            ),
        )
        conn.execute(
            "INSERT INTO dense_vector VALUES (?,?,?)",
            (r["record_id"], 1024, vec.tobytes()),
        )
    conn.commit()
    conn.close()

    rates = compute_index_b_margin_tie_rates(sidecar_b, [v1], margin_threshold=0.05)
    assert "proposition_unit" in rates

    graph_db = tmp_path / "graph.sqlite"
    gconn = sqlite3.connect(str(graph_db))
    gconn.execute(
        "CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT)"
    )
    gconn.execute("INSERT INTO assertion_evidence VALUES ('a1', 'ev1', 'supports')")
    gconn.commit()
    gconn.close()

    sidecar_a = tmp_path / "sidecar_a.sqlite"
    aconn = sqlite3.connect(str(sidecar_a))
    aconn.executescript(SIDECAR_SCHEMA)
    edge_rec = {
        **rec1,
        "record_id": "edge-1",
        "record_type": "edge",
        "retrieval_kind": "relationship_assertion",
        "extensions": {"com.partner_advisory.nee.graph-object-id": "a1"},
    }
    aconn.execute(
        "INSERT INTO retrieval_record VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            edge_rec["record_id"],
            edge_rec["lane"],
            edge_rec["record_type"],
            edge_rec["namespace"],
            edge_rec["retrieval_text_hash"],
            edge_rec["retrieval_kind"],
            edge_rec["epistemic_status"],
            edge_rec["provenance_kind"],
            edge_rec["answer_use"],
            json.dumps(edge_rec),
        ),
    )
    aconn.commit()
    aconn.close()

    reach = verify_edge_fact_reachability(graph_db, sidecar_b, sidecar_a)
    assert reach["total_edge_facts"] == 1
    assert reach["reachable_via_traversal"] == 1
    assert reach["target_100pct_met"] is True


def test_query_l1_shadow_builders_attempt_observation_and_terminal() -> None:
    plan = seal_artifact({"contract_id": "plan", "question": "test"})
    plan_digest = plan["canonical_digest"]["value"]

    manifest = seal_artifact(
        {
            "query_plan_digest": plan_digest,
            "authorization_scope_digest": "b" * 64,
            "requirements": [{"requirement_id": "req-1", "required": True}],
        }
    )
    manifest_digest = manifest["canonical_digest"]["value"]

    report = seal_artifact(
        {
            "query_plan_digest": plan_digest,
            "coverage_manifest_digest": manifest_digest,
            "selected_record_ids_by_lane": {"approved": ["rec-1"]},
            "requirement_outcomes": [{"requirement_id": "req-1", "outcome": "covered"}],
        }
    )
    pair = {
        "generation_id": "gen-1",
        "graph_sha256": "e" * 64,
        "sidecar_sha256": "f" * 64,
        "embedding_backend": "bge-m3",
        "model_id": "BAAI/bge-m3",
        "model_revision": "rev1",
    }
    attempt, action = build_attempt_zero(
        plan=plan,
        coverage_manifest=manifest,
        evidence_report=report,
        active_pair=pair,
        graph_sha256_before="e" * 64,
        graph_sha256_after="e" * 64,
    )
    assert attempt["attempt_ordinal"] == 0
    assert action.attempt_id == attempt["attempt_id"]

    obs, _prog = build_observation_zero(
        plan=plan,
        coverage_manifest=manifest,
        evidence_report=report,
        attempt_zero=attempt,
        graph_sha256_before="e" * 64,
        graph_sha256_after="e" * 64,
    )
    assert obs["progress"] == "complete"

    term = build_shadow_terminal(
        plan=plan,
        coverage_manifest=manifest,
        evidence_report=report,
        attempt_chain=[attempt],
        observation=obs,
        budget=BudgetState.initial(),
        controller_terminal_reason="L1_COMPLETE",
        runtime_terminal_outcome="ANSWERED",
        runtime_next_stage="L2D",
        graph_sha256_before="e" * 64,
        graph_sha256_after="e" * 64,
    )
    assert term["terminal_reason"] == "SHADOW"

    proj = {"route_digest": "r1", "terminal_outcome": "ANSWERED"}
    comp = build_off_shadow_comparison(off_projection=proj, shadow_projection=proj)
    assert comp["status"] == "PASS"

    with pytest.raises(ShadowReceiptError):
        build_attempt_zero(
            plan=plan,
            coverage_manifest=seal_artifact({**manifest, "query_plan_digest": "9" * 64}),
            evidence_report=report,
            active_pair=pair,
            graph_sha256_before="e" * 64,
            graph_sha256_after="e" * 64,
        )
