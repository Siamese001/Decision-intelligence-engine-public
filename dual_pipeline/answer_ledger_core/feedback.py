"""
dual_pipeline/answer_ledger_core/feedback.py

Feedback ingestion and cryptographic binding for AnswerRecords.
Enforces origin authenticity (rejecting synthetic origins via validate_approval_origin).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.answer_ledger_core.models import AnswerFeedback
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from dual_pipeline.hitl_governance import validate_approval_origin

logger = logging.getLogger("dual_pipeline.answer_ledger.feedback")

VALID_RATINGS = {"useful", "not_useful", "wrong"}


def _build_feedback_proposal_artifact(
    proposal_id: str, answer_id: str, clean_rating: str, note: Optional[str],
    current_val: float, proposed_val: float, p_hash: str,
) -> Dict[str, Any]:
    from dual_pipeline.contracts import create_artifact
    from dual_pipeline.learning_engine_core.provenance import PROVENANCE_EXTENSION_KEY, build_signed_provenance

    ev_ids, run_ids = [f"feedback-{answer_id}"], [f"run-copilot-feedback-{p_hash[:8]}"]
    problem = f"Operator negative feedback ({clean_rating}) recorded on copilot answer {answer_id}: {note or 'Identified quality or grounding defect'}."
    proposed_change = f"Tighten copilot speak gate min speaker confidence threshold from {current_val} to {proposed_val} to prevent marginal responses from speaking."
    param = "copilot.speak_gate.min_speaker_confidence"
    prov = build_signed_provenance(
        proposal_id=proposal_id, evidence_event_ids=ev_ids, run_ids=run_ids,
        proposed_change=proposed_change, replay_definition={"answer_id": answer_id, "rating": clean_rating, "target_parameter": param},
    )
    return create_artifact(
        "learning-change-proposal", proposal_id, proposal_id=proposal_id, run_ids=run_ids,
        proposal_type="copilot_grounding", status="proposed", approval_required=True, auto_apply=False,
        risk="low", created_at=datetime.now(timezone.utc).isoformat(), problem=problem,
        proposed_change=proposed_change, evidence_event_ids=ev_ids,
        evaluation_plan=[
            "Replay sealed holdout evaluation cases to verify silence-precision holds >= 0.80",
            "Verify groundedness meets or exceeds frames baseline (>= 0.98)",
            "Verify zero automated mutation (auto_apply strictly false)",
        ],
        extensions={
            "com.partner_advisory.nee.calibration": {
                "auto_mutation_permitted": False, "current_value": current_val, "proposed_value": proposed_val,
                "requires_release_gate": True, "target_parameter": param,
            },
            "com.partner_advisory.nee.learning": {
                "baseline_score": 0.72, "category": "copilot_grounding", "cohort_key": f"operator_feedback:{clean_rating}",
                "current_value": current_val, "parameter_name": param, "proposed_value": proposed_val,
                "target_after": 0.86, "target_before": 0.72,
                "target_scores": {"groundedness_accuracy": 0.86, "silence_precision": 0.85}, "target_type": "copilot_speak_gate",
            },
            PROVENANCE_EXTENSION_KEY: prov,
        },
    )


def synthesize_feedback_threshold_proposal(
    answer_id: str,
    rating: str,
    note: Optional[str] = None,
    proposals_dir: Optional[Path] = None,
    current_threshold: float = 0.60,
    delta: float = 0.05,
) -> Optional[Dict[str, Any]]:
    """Synthesizes a bounded learning-change-proposal:1.0.0 artifact from negative operator feedback.
    Enforces invariant: auto_apply is ALWAYS False, approval_required is ALWAYS True.
    """
    clean_rating = str(rating or "").strip().lower()
    if clean_rating not in ("not_useful", "wrong"):
        return None

    raw_key = f"{answer_id}:{clean_rating}:{note or ''}"
    p_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    proposal_id = f"lcp-copilot-feedback-{p_hash}"

    current_val = round(max(0.50, min(0.90, current_threshold)), 2)
    bounded_delta = round(max(0.0, min(0.05, delta)), 2)
    proposed_val = round(min(0.90, current_val + bounded_delta), 2)
    proposal_data = _build_feedback_proposal_artifact(
        proposal_id, answer_id, clean_rating, note, current_val, proposed_val, p_hash
    )

    target_dir = proposals_dir
    if target_dir is None:
        default_dir = Path("reports/learning_proposals")
        if default_dir.is_dir():
            target_dir = default_dir

    if target_dir is not None:
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        out_file = target_dir / f"{proposal_id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(proposal_data, f, indent=2, sort_keys=True)

    return proposal_data


def record_operator_feedback(
    ledger: AnswerLedger,
    answer_id: str,
    rating: str,
    note: Optional[str] = None,
    origin: str = "USER_EXPLICIT",
    proposals_dir: Optional[Path] = None,
) -> AnswerFeedback:
    """Records explicit operator feedback on an AnswerRecord, enforcing authentic human origin."""
    normalized_rating = rating.strip().lower()
    if normalized_rating not in VALID_RATINGS:
        raise ValueError(
            f"INVALID_RATING: '{rating}' is not valid. Must be one of: {sorted(VALID_RATINGS)}"
        )

    # Enforce authentic operator origin (anti-synthetic stop hook defense)
    is_valid_origin, origin_reason = validate_approval_origin({"source": origin})
    if not is_valid_origin:
        raise ValueError(f"SYNTHETIC_FEEDBACK_REJECTED: {origin_reason}")

    answer = ledger.get_answer(answer_id)
    if not answer:
        raise KeyError(f"ANSWER_NOT_FOUND: Answer record '{answer_id}' does not exist in ledger.")

    proposal = None
    if normalized_rating in ("not_useful", "wrong"):
        proposal = synthesize_feedback_threshold_proposal(
            answer_id=answer.answer_id,
            rating=normalized_rating,
            note=note,
            proposals_dir=proposals_dir,
        )

    feedback = AnswerFeedback(
        answer_id=answer.answer_id,
        answer_digest=answer.record_digest,
        feedback_type="explicit",
        rating=normalized_rating,
        origin=origin,
        note=note,
        weight=1.0,
        proposal_id=proposal.get("proposal_id") if proposal else None,
    )
    return ledger.append_feedback(feedback)


def record_implicit_feedback(
    ledger: AnswerLedger,
    answer_id: str,
    cited_ids_found_in_recap: bool,
    note: Optional[str] = None,
    weight: float = 0.5,
) -> AnswerFeedback:
    """Records implicit feedback derived from post-meeting recap/projection mentions."""
    answer = ledger.get_answer(answer_id)
    if not answer:
        raise KeyError(f"ANSWER_NOT_FOUND: Answer record '{answer_id}' does not exist in ledger.")

    rating = "useful" if cited_ids_found_in_recap else "not_useful"
    default_note = (
        "Cited claims were affirmed in post-meeting recap deliverables."
        if cited_ids_found_in_recap
        else "Cited claims were not mentioned in post-meeting recap deliverables."
    )

    feedback = AnswerFeedback(
        answer_id=answer.answer_id,
        answer_digest=answer.record_digest,
        feedback_type="implicit",
        rating=rating,
        origin="SYSTEM_INGESTION",
        note=note or default_note,
        weight=max(0.0, min(1.0, weight)),
    )
    return ledger.append_feedback(feedback)
