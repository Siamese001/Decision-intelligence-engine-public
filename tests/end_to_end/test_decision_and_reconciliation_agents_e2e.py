"""End-to-end verification of CounterfactualDevilAdvocateAgent, CrossArtifactReconciliationAgent, and 5-Agent Self-Correction."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

from dual_pipeline.decision_engine_core.devil_advocate_agent import (
    CounterfactualDevilAdvocateAgent,
)
from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    run_5_agent_document_intelligence,
)
from dual_pipeline.transcript_qa_core.reconciliation_agent import (
    CrossArtifactReconciliationAgent,
)


def test_counterfactual_devil_advocate_challenges_top_option_and_escalates_narrow_margin_e2e() -> None:
    """Verifies that disconfirming HAS_RISK and CONTRADICTS edges penalize option confidence and trigger HITL."""
    agent = CounterfactualDevilAdvocateAgent(max_steps=2, max_tool_calls=4)
    options = [
        {"option_id": "opt_cloud_migration", "label": "Immediate Lift-and-Shift", "confidence": 0.82},
        {"option_id": "opt_phased_hybrid", "label": "Phased Hybrid Rollout", "confidence": 0.64},
    ]
    evidence_records = [
        {
            "evidence_id": "ev-risk-101",
            "option_id": "opt_cloud_migration",
            "predicate": "HAS_RISK",
            "claim_text": "Immediate Lift-and-Shift introduces latency spikes on legacy database.",
            "currentness_state": "current",
            "lane": "approved",
        },
        {
            "evidence_id": "ev-contra-102",
            "option_id": "opt_cloud_migration",
            "predicate": "CONTRADICTS",
            "claim_text": "Immediate Lift-and-Shift contradicts zero-downtime SLA constraint.",
            "currentness_state": "stale",
            "lane": "approved",
        },
        {
            "evidence_id": "ev-cand-999",
            "option_id": "opt_cloud_migration",
            "predicate": "SUPPORTS",
            "claim_text": "Unverified candidate note.",
            "lane": "candidate",
        },
    ]

    report = agent.challenge(options=options, evidence_records=evidence_records)

    assert report.challenged_option_id == "opt_cloud_migration"
    assert report.original_confidence == 0.82
    assert report.penalized_confidence < report.original_confidence
    assert report.post_challenge_margin <= 0.20
    assert report.requires_hitl_escalation is True
    assert "ev-risk-101" in report.disconfirming_evidence_ids
    assert "ev-contra-102" in report.disconfirming_evidence_ids
    assert "ev-contra-102" in report.stale_evidence_ids
    assert "ev-cand-999" in report.candidate_lane_excluded_ids
    assert report.steps_executed == 2
    assert report.terminal_outcome == "COMPLETED"
    assert len(report.claim_checks) >= 2


def test_cross_artifact_reconciliation_repairs_owner_and_quote_drift_without_touching_output_01_e2e() -> None:
    """Verifies surgical repair of Output 02/03 drift while keeping Output 01 SHA-256 frozen."""
    output_01 = (
        "# Verbatim Transcript\n"
        "Alice Rivera: We will complete the schema migration by Friday afternoon to unblock release testing.\n"
        "Marcus Chen: Agreed, I will review the pull request once the migration script runs.\n"
    )
    output_02 = (
        "# Structured Meeting Notes\n"
        "## Action Items\n"
        "- Complete schema migration by Friday.\n"
        "  **Owner:** Alice Rivera\n"
        '  [Quote: "We will finish the schema migration by Friday afternoon to unblock"]\n'
    )
    output_03 = (
        "# Executive Summary\n"
        "## Action Items\n"
        "- Complete schema migration by Friday. Owner: Bob Vance\n"
    )

    reconciler = CrossArtifactReconciliationAgent(max_steps=2, max_tool_calls=4)
    result = reconciler.reconcile(
        output_01_text=output_01,
        output_02_text=output_02,
        output_03_text=output_03,
        speaker_turn_shares={"Alice Rivera": 0.55, "Marcus Chen": 0.45},
    )

    assert result.output_01_immutable_verified is True
    assert result.parity_passed is True
    assert result.speaker_collapse_detected is False
    assert result.requires_hitl_escalation is False
    assert len(result.directives) >= 1
    assert "Owner: Alice Rivera" in result.repaired_output_03
    assert "Bob Vance" not in result.repaired_output_03
    assert "to unblock release testing." in result.repaired_output_02


def test_five_agent_doc_intelligence_self_corrects_primacy_bias_e2e() -> None:
    """Verifies that Agent 5 primacy bias detection triggers secondary role recovery."""
    doc_text = (
        "The Product Owner defines backlog priorities. "
        "The Solution Owner collaborates with the Client Product Owner and Scrum Master "
        "while the Agile Coach supports delivery teams."
    )

    call_idx = 0

    def fake_llm(_sys: str, _usr: str) -> Dict[str, Any]:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            # Simulate primacy bias: Agent 1 only extracted the first role
            return {
                "normalized_entities": [
                    {"id": "role:product-owner", "type": "role", "name": "Product Owner", "description": "Owns backlog."}
                ]
            }
        if call_idx == 2:
            return {"relationships": []}
        return {
            "coverage_score": 0.25,
            "primacy_bias_detected": True,
            "arbiter_verdict": "BLOCKED",
            "notes": "Single-role collapse onto Product Owner.",
        }

    with patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence._invoke_agent_llm",
        side_effect=fake_llm,
    ):
        report = run_5_agent_document_intelligence(doc_text, "Agile Roles Guide")

    assert report.primacy_bias_detected is False
    assert report.arbiter_verdict == "PASS"
    assert report.total_entities_detected >= 4
    assert "[Self-Corrected:" in report.arbiter_notes
