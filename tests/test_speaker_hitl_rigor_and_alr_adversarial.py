"""
tests/test_speaker_hitl_rigor_and_alr_adversarial.py

Adversarial & Edge-Case Verification for Wave 6 (covering >= 3 mandatory categories):
1. Invariant bypass attempt:
   - Self-asserted `hitl_receipt_bound=True` in metadata without an Ed25519-signed ledger receipt
     is rejected fail-closed by `verify_authentic_hitl_receipt_binding` and `enforce_pre_l2_speaker_hitl_gate`.
   - Premature pre-prompt `"APPROVE"` ledger signing is prevented in `guard_collapse_or_evict_for_hitl`
     until the interactive resolver actually completes.
2. Boundary payload:
   - Exact 20.0% (`0.20`) ambiguity margin boundary (`top=0.70, second=0.50`) triggers
     `needs_operator_decision` and halts at `enforce_pre_l2_speaker_hitl_gate`.
3. Malformed input:
   - Collapsed single-line turn markers (`BEFORE 1 ... TARGET ...`) or missing context turns
     with `fail_closed=True` raise `ValueError` in `validate_visible_speaker_context` and
     `build_hitl_review_payload`.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.u0_speaker_hitl_gate import (
    enforce_pre_l2_speaker_hitl_gate,
    verify_authentic_hitl_receipt_binding,
)
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    needs_operator_decision,
)
from dual_pipeline.speaker_attribution_core.conflict_detector import guard_collapse_or_evict_for_hitl
from dual_pipeline.speaker_attribution_core.review_context_render import (
    build_hitl_review_payload,
    validate_visible_speaker_context,
)


def test_forged_hitl_receipt_bound_rejected_fail_closed(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt — forged hitl_receipt_bound=True without ledger signature fails closed."""
    fake_ledger = tmp_path / "empty_ledger.json"
    fake_ledger.write_text("[]", encoding="utf-8")

    meta = {
        "meeting_name": "Adversarial_Bypass_Test",
        "date": "10/29/2026",
        "project": "sec_audit",
        "roster": ["Amit Ayer", "Alice Chen"],
        "hitl_receipt_bound": True,
        "hitl_ledger_path": str(fake_ledger),
    }
    transcript = (
        "**[00:01] Amit Ayer:** Checking receipt verification.\n\n"
        "**[00:05] Alice Chen:** Forged flags must be rejected.\n"
    )
    with pytest.raises(ValueError, match="FORGED_HITL_RECEIPT_BOUND"):
        verify_authentic_hitl_receipt_binding(meta, transcript=transcript, ledger_path=fake_ledger)
    assert meta["hitl_receipt_bound"] is False


def test_guard_collapse_does_not_sign_ledger_before_operator_prompt(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt — guard_collapse_or_evict_for_hitl must not sign APPROVE before prompt."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    ledger_file.write_text("[]", encoding="utf-8")

    # 12 turns all attributed to Speaker 1 in a 3-person meeting -> single_speaker_collapse
    turns = "\n\n".join(
        f"**[00:{i:02d}] Speaker 1:** This is turn number {i} in a collapsed transcript."
        for i in range(1, 13)
    )
    roster = ["Amit Ayer", "Alice Chen", "Bob Smith"]
    meta = {
        "meeting_name": "Collapse_Pre_Sign_Audit",
        "date": "10/29/2026",
        "project": "sec_audit",
        "roster": roster,
        "hitl_ledger_path": str(ledger_file),
    }
    existing_map = {"Speaker 1": "Amit Ayer"}

    evicted = guard_collapse_or_evict_for_hitl(
        transcript=turns,
        roster=roster,
        metadata=meta,
        interactive_resolver=lambda q, opts: opts[0],
        existing_map=existing_map,
    )
    assert "Speaker 1" in evicted
    assert "Speaker 1" not in existing_map
    assert meta.get("collapse_evicted_pending_receipt") is True
    # Crucially, no premature collapse_approval_receipt was written before the interactive prompt ran!
    assert "collapse_approval_receipt" not in meta


def test_ssot_20pct_margin_boundary_triggers_hitl() -> None:
    """Category: Boundary payload — exact 0.20 confidence spread requires operator decision."""
    thresh = get_hitl_ambiguity_margin_threshold()
    assert abs(thresh - 0.20) < 1e-6
    assert needs_operator_decision(0.70, 0.50, threshold=thresh) is True
    assert needs_operator_decision(0.7001, 0.50, threshold=thresh) is False

    meta = {
        "meeting_name": "Boundary_Margin_Meeting",
        "date": "10/29/2026",
        "project": "test_proj",
        "roster": ["Amit Ayer", "Alice Chen"],
        "speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Alice Chen"},
        "cluster_candidate_scores": {
            "Speaker 2": [
                {"attendee_name": "Alice Chen", "confidence": 0.65},
                {"attendee_name": "Amit Ayer", "confidence": 0.45},
            ]
        },
    }
    transcript = (
        "**[00:01] Amit Ayer:** First turn.\n\n"
        "**[00:05] Alice Chen:** Second turn.\n"
    )
    with pytest.raises(HITLRequiredError) as exc_info:
        enforce_pre_l2_speaker_hitl_gate(transcript=transcript, metadata=meta)
    assert exc_info.value.hitl_type == "pre_l2_speaker_margin_gate"


def test_malformed_single_line_review_context_rejected() -> None:
    """Category: Malformed input — collapsed turn markers on one line or missing target raises ValueError."""
    collapsed_context = (
        "Meeting/source: test_src\n\n"
        "BEFORE 2 [00:01] Amit: Hi BEFORE 1 [00:02] Alice: Hello\n\n"
        "TARGET [00:03] **UNRESOLVED — Speaker 1**: Test\n\n"
        "AFTER 1 [00:04] Amit: Ok\n\n"
        "AFTER 2 [00:05] Alice: Bye\n"
    )
    with pytest.raises(ValueError, match="multiple turn markers"):
        validate_visible_speaker_context(collapsed_context)

    with pytest.raises(ValueError, match="FAIL_CLOSED_REVIEW_CONTEXT"):
        build_hitl_review_payload(
            transcript="**[00:01] Amit Ayer:** Only one speaker present.",
            target_speaker="NonExistentSpeaker",
            question="Who is speaking?",
            candidates=[],
            source_id="test_src",
            fail_closed=True,
        )
