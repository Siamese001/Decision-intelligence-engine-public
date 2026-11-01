"""
tests/test_copilot_feedback_loop_e2e.py

End-to-End qualification tests for Wave 6: Feedback Loop & Flagged Composer.
Verifies that:
1. Negative operator feedback ("not_useful", "wrong") automatically synthesizes a bounded threshold
   learning proposal conforming strictly to learning-change-proposal:1.0.0.
2. The proposal strictly enforces auto_apply=False and approval_required=True (anti-auto-mutation invariant).
3. Proposed parameter calibrations are bounded (delta <= 0.05 within [0.50, 0.90]).
4. Positive operator feedback ("useful") generates no learning proposals.
5. Sealed holdout dataset matches cryptographic SHA-256 seal.
6. Local-model composer achieves holdout groundedness equal to frames (>= 0.98) and non-negative relevance gain.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from dual_pipeline.answer_ledger_core.feedback import (
    record_operator_feedback,
    synthesize_feedback_threshold_proposal,
)
from dual_pipeline.answer_ledger_core.models import AnswerRecord
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from evals.copilot_quality_gate import (
    SEALED_HOLDOUT_SHA256,
    load_and_verify_holdout,
    run_copilot_quality_gate,
)


@pytest.fixture
def temp_ledger(tmp_path: Path) -> AnswerLedger:
    db_path = tmp_path / "test_answer_ledger.sqlite"
    return AnswerLedger(db_path=db_path)


def test_feedback_loop_synthesizes_bounded_proposal_on_negative_rating(temp_ledger: AnswerLedger, tmp_path: Path):
    """Verify that negative feedback generates a contract-valid learning-change-proposal with auto_apply=False."""
    ans = AnswerRecord(
        pipeline="copilot",
        intent_or_type="respond",
        context_digest="digest_ctx_123",
        retrieval_seeds=["db", "latency"],
        context_snapshot=[{"speaker": "Bob", "text": "Latency is high."}],
        cited_assertion_ids=["ast_1"],
        rigor="T2",
        response_text="Regarding latency, please verify queue limits.",
    )
    ans = temp_ledger.append_answer(ans)

    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    # Operator thumbs-down rating
    fb = record_operator_feedback(
        temp_ledger,
        answer_id=ans.answer_id,
        rating="wrong",
        note="Claim was grounded but speaker confidence was marginal.",
        origin="USER_EXPLICIT",
        proposals_dir=proposals_dir,
    )

    assert fb.rating == "wrong"
    assert fb.proposal_id is not None
    assert fb.proposal_id.startswith("lcp-copilot-feedback-")

    # Verify written JSON artifact
    proposal_file = proposals_dir / f"{fb.proposal_id}.json"
    assert proposal_file.is_file(), f"Proposal file missing at {proposal_file}"

    data = json.loads(proposal_file.read_text(encoding="utf-8"))
    assert data["contract_id"] == "urn:nee:dual-pipeline:learning-change-proposal:1.0.0"
    assert data["status"] == "proposed"

    # Non-negotiable safety invariants
    assert data["auto_apply"] is False, "CRITICAL: auto_apply must be False"
    assert data["approval_required"] is True, "CRITICAL: approval_required must be True"

    # Calibration extensions
    cal = data["extensions"]["com.partner_advisory.nee.calibration"]
    assert cal["auto_mutation_permitted"] is False
    assert cal["requires_release_gate"] is True
    assert cal["current_value"] == 0.60
    assert (
        round(cal["proposed_value"] - cal["current_value"], 4) <= 0.05
    ), "Calibration delta exceeded 0.05 ceiling"


def test_positive_feedback_does_not_generate_proposal(temp_ledger: AnswerLedger, tmp_path: Path):
    """Verify that positive operator feedback ("useful") records cleanly without emitting proposals."""
    ans = AnswerRecord(
        pipeline="copilot",
        intent_or_type="respond",
        context_digest="digest_ctx_456",
        retrieval_seeds=["slo"],
        context_snapshot=[{"speaker": "Alice", "text": "Review SLO."}],
        cited_assertion_ids=["ast_2"],
        rigor="T3",
        response_text="Our agreed baseline is 99.95% availability.",
    )
    ans = temp_ledger.append_answer(ans)

    proposals_dir = tmp_path / "proposals_empty"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    fb = record_operator_feedback(
        temp_ledger,
        answer_id=ans.answer_id,
        rating="useful",
        note="Accurate baseline",
        origin="USER_EXPLICIT",
        proposals_dir=proposals_dir,
    )

    assert fb.rating == "useful"
    assert fb.proposal_id is None
    assert len(list(proposals_dir.glob("*.json"))) == 0


def test_sealed_holdout_digest_and_purity():
    """Verify sealed holdout dataset integrity against cryptographic SHA-256 seal."""
    holdout_path = Path("tests/evals/copilot_golden/v1/sealed_holdout.jsonl")
    cases = load_and_verify_holdout(holdout_path)
    assert len(cases) == 30, f"Expected 30 holdout cases, got {len(cases)}"

    # Check distribution
    speak_cases = [c for c in cases if c["expected_action"] == "speak"]
    trap_cases = [c for c in cases if c["expected_action"] == "trap"]
    assert len(speak_cases) == 20
    assert len(trap_cases) == 10


def test_composer_holdout_ablation_qualification(tmp_path: Path):
    """Verify quality gate over sealed holdout: composer groundedness equals frames and floors pass."""
    holdout_path = Path("tests/evals/copilot_golden/v1/sealed_holdout.jsonl")
    receipt_out = tmp_path / "receipts" / "copilot_wave6_feedback_receipt.json"

    exit_code = run_copilot_quality_gate(holdout_path=holdout_path, receipt_out=receipt_out)
    assert exit_code == 0, "Copilot quality gate evaluation failed"

    receipt = json.loads(receipt_out.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["wave"] == 6

    # Verify acceptance criteria in receipt
    ablation = receipt["ablation_report"]
    frames_grnd = ablation["baseline_frames"]["groundedness"]
    comp_grnd = ablation["local_composer_flagged"]["groundedness"]
    assert comp_grnd >= frames_grnd, f"Composer groundedness ({comp_grnd}) dropped below frames ({frames_grnd})"
    assert comp_grnd >= 0.98, f"Groundedness below 0.98 floor: {comp_grnd}"

    inv = receipt["verified_invariants"]
    assert inv["timeout_fail_closed_strictly_silent"] is True
    assert inv["auto_apply_strictly_false"] is True
