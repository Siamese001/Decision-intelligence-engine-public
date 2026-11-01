"""Tests for Wave 4: Temporal & Lifecycle Horizon Awareness."""
from __future__ import annotations

from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
)
from dual_pipeline.decision_engine_core.assessment import assess_options
from dual_pipeline.decision_engine_core.quantitative_math import (
    evaluate_temporal_constraint_breaches,
    parse_temporal_horizon,
)


def test_parse_temporal_horizon_extracts_quarters_and_days() -> None:
    q2 = parse_temporal_horizon("Q2 2026 rollout")
    assert q2["quarter"] == "Q2 2026"
    assert q2["horizon_days"] == 90
    assert q2["is_specified"] is True

    d14 = parse_temporal_horizon("14 days notice")
    assert d14["horizon_days"] == 14
    assert d14["is_specified"] is True

    unspec = parse_temporal_horizon("unspecified")
    assert unspec["is_specified"] is False
    assert unspec["horizon_days"] is None


def test_build_decision_frame_extracts_horizon_and_stores_extension() -> None:
    frame = build_decision_frame(
        question="Should we scale pods in Q2 2026 given operating_model_fit and governance_and_execution_risk?"
    )
    assert frame["time_horizon"] == "Q2 2026"
    assert frame["extensions"]["com.partner_advisory.nee.temporal-horizon"]["quarter"] == "Q2 2026"
    assert frame["extensions"]["com.partner_advisory.nee.temporal-horizon"]["horizon_days"] == 90


def test_temporal_constraint_breaches_flag_14_day_deprovisioning_conflict() -> None:
    constraints = [
        {
            "record_id": "rec_deprovision_14d",
            "title": "IT Deprovisioning Notice Window",
            "text": "Offboarding requires a mandatory two weeks prior (14-day) IT deprovisioning notice window.",
            "evidence_ids": ["ev_deprov_1"],
            "constraint_category": "schedule_notice",
        }
    ]
    breaches = evaluate_temporal_constraint_breaches(
        option_label="Blocked / Non-Viable Elements: Immediate Staggered SOW Local Pods",
        bound_records=[],
        constraints=constraints,
        time_horizon="30 days",
    )
    assert len(breaches) == 1
    assert breaches[0]["constraint_record_id"] == "rec_deprovision_14d"
    assert breaches[0]["notice_window_days"] == 14
    assert breaches[0]["evidence_id"] == "ev_deprov_1"


def test_assess_options_records_temporal_breach_in_constraint_violations_and_counterevidence() -> None:
    frame = build_decision_frame(
        question="Evaluate local pod proposal given governance_and_execution_risk",
        options=[
            "Immediate Unbuffered Local Pod Rollout",
            "Synchronized 6-Month SOW Model",
        ],
        criteria=["governance_and_execution_risk"],
        time_horizon="30 days",
    )
    pack = build_decision_evidence_pack(
        query_result={
            "run_id": "run_wave4",
            "question": frame["decision_question"],
            "generation_id": "gen_1",
            "graph_snapshot": "snap_1",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "bge-m3",
            "bm25_executed": True,
            "dense_executed": True,
            "sparse_executed": True,
            "colbert_executed": True,
            "graph_traversal_executed": True,
        },
        evidence_packet={
            "approved": [
                {
                    "record": {
                        "record_id": "rec_deprov",
                        "title": "Deprovisioning Notice",
                        "retrieval_text": "Mandatory two weeks prior (14-day) IT deprovisioning notice window.",
                        "record_type": "claim",
                        "answer_use": "decision_constraint",
                        "evidence_ids": ["ev_deprov_14d"],
                        "currentness_state": "current",
                    },
                    "retrieval_metadata": {"answer_use": "decision_constraint"},
                }
            ],
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
        },
    )
    options = [
        {
            "option_id": frame["options"][0]["option_id"],
            "label": "Immediate Unbuffered Local Pod Rollout",
            "source": "caller",
            "rationale": "Immediate rollout",
            "evidence_ids": [],
            "record_ids": [],
        },
        {
            "option_id": frame["options"][1]["option_id"],
            "label": "Synchronized 6-Month SOW Model",
            "source": "constraint_satisfaction_synthesis",
            "rationale": "Synchronized SOW with 14-day buffer",
            "evidence_ids": [],
            "record_ids": [],
        },
    ]
    assessments = assess_options(frame=frame, evidence_pack=pack, options=options)
    by_opt = {a["option_id"]: a for a in assessments}
    unbuffered = by_opt[options[0]["option_id"]]
    mitigated = by_opt[options[1]["option_id"]]

    assert "rec_deprov" in unbuffered["constraint_violations"]
    assert "ev_deprov_14d" in unbuffered["counterevidence_ids"]
    assert unbuffered["assessment"] == "OPPOSES"
    assert mitigated["constraint_violations"] == []


def test_discriminating_recovery_triggers_on_mixed_assessments() -> None:
    frame = build_decision_frame(
        question="Choose between Option A and Option B given governance_and_execution_risk",
        options=["Option A", "Option B"],
        criteria=["governance_and_execution_risk"],
    )
    initial_packet = {
        "approved": [
            {
                "record": {
                    "record_id": "rec_a",
                    "title": "Option A",
                    "retrieval_text": "Option A addresses governance_and_execution_risk.",
                    "record_type": "assertion",
                    "evidence_ids": ["ev_a"],
                    "currentness_state": "current",
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            },
            {
                "record": {
                    "record_id": "rec_b",
                    "title": "Option B",
                    "retrieval_text": "Option B addresses governance_and_execution_risk.",
                    "record_type": "assertion",
                    "evidence_ids": ["ev_b"],
                    "currentness_state": "current",
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            },
        ],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [
            {"description": "Option B has a governance_and_execution_risk conflict", "citations": ["ev_b_conflict"]}
        ],
        "gaps": [],
    }
    pack = build_decision_evidence_pack(
        query_result={
            "run_id": "run_mixed",
            "generation_id": "gen_1",
            "graph_snapshot": "snap_1",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "bge-m3",
        },
        evidence_packet=initial_packet,
    )
    resolver_calls: list[str] = []

    def resolver(question: str, current_pack: dict) -> dict:
        resolver_calls.append(question)
        return current_pack

    # Force a tie so candidate_winner is None while MIXED assessment is present
    initial_packet["counterevidence"].append(
        {"description": "Option A also has a conflict", "citations": ["ev_a_conflict"]}
    )
    tied_pack = build_decision_evidence_pack(
        query_result={
            "run_id": "run_mixed_tied",
            "generation_id": "gen_1",
            "graph_snapshot": "snap_1",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "bge-m3",
        },
        evidence_packet=initial_packet,
    )
    evaluate_decision(
        frame=frame,
        evidence_pack=tied_pack,
        answer=None,
        execution={"query_run_id": "run_mixed_tied"},
        discriminating_resolver=resolver,
    )
    assert len(resolver_calls) == 1
