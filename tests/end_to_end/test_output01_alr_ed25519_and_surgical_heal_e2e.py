"""
tests/end_to_end/test_output01_alr_ed25519_and_surgical_heal_e2e.py

Class B E2E lifecycle test verifying:
1. End-to-end Output 01 Ambiguity Lock Register (ALR) Ed25519 cryptographic signing and verification (Gate 1.7).
2. End-to-end deterministic structural defect healing via SurgicalRemediator (<50ms, 0 LLM calls).
3. Anti-forgery preservation through SurgicalRemediator and Gate 1.7 fail-closed enforcement.
4. Gate 1.10 entity context scan over Output 01 dialogue.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
    verify_alr_cryptographic_receipt,
)
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    evaluate_referenced_entity_context_gate,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import (
    format_ambiguity_lock_register,
)


def _setup_test_ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "hitl_decision_ledger.json"
    ledger.write_text(json.dumps({"decisions": [], "priors": {}}), encoding="utf-8")
    return ledger


# ---------------------------------------------------------------------------
# 1. E2E ALR Ed25519 Cryptographic Signing & Gate 1.7 Verification
# ---------------------------------------------------------------------------

def test_output01_alr_ed25519_signing_and_verification_e2e(tmp_path: Path) -> None:
    """E2E lifecycle: generate signed approval, format ALR with receipt ID, and verify Gate 1.7 passes."""
    ledger = _setup_test_ledger(tmp_path)
    priv_key, pub_key = generate_hitl_keypair()

    attendees = ["Alice Martin", "Bob Vance", "Charlie Day"]
    subject_dict = {
        "meeting_name": "Quarterly_Architecture_Sync",
        "meeting_date": "09/30/2026",
        "project": "nea",
        "attendees": sorted(attendees),
    }
    subject_sha256 = compute_alr_subject_digest(subject_dict)

    # 1. Sign approval and persist to ledger
    entry = create_signed_hitl_approval_entry(
        case_ids=["SPK-001"],
        subject_sha256=subject_sha256,
        operator_id="operator_principal",
        decision="APPROVE",
        private_key=priv_key,
        public_key=pub_key,
        meeting_slug="quarterly_architecture_sync",
        meeting_date="09/30/2026",
    )
    append_signed_approval_to_ledger(entry, ledger_path=ledger)
    verified_entry_id = entry["entry_id"]

    metadata: Dict[str, Any] = {
        "meeting_name": "Quarterly_Architecture_Sync",
        "meeting_slug": "quarterly_architecture_sync",
        "date": "09/30/2026",
        "project": "nea",
        "attendees": attendees,
        "subject_sha256": subject_sha256,
        "hitl_ledger_path": str(ledger),
        "hitl_resolutions": {
            "amb_spk_01": {
                "ambiguity": "Speaker Attribution: Alice Martin",
                "locator": "[00:00:00 – Session End]",
                "statement_id": "stmt-01",
                "resolution": "Attributed to Alice Martin per voice profile.",
                "basis": "Operator Confirmed (Acoustic Grounding)",
                "verified_entry_id": verified_entry_id,
            }
        },
    }

    # 2. Format ALR via lexical normalizer
    speaker_map = {"SPEAKER_00": "Alice Martin", "SPEAKER_01": "Bob Vance"}
    alr_table = format_ambiguity_lock_register(metadata, speaker_map)
    assert f"[Ed25519 Receipt: {verified_entry_id[:16]}]" in alr_table

    # 3. Assemble Output 01 draft
    output_01 = (
        f"# Forensic Transcript\n\n"
        f"**Date:** 09/30/2026\n\n"
        f"### [00:01:00]\n\n"
        f"**Alice Martin:** Beginning quarterly architecture sync.\n\n"
        f"### [00:01:10]\n\n"
        f"**Bob Vance:** Seconded.\n\n"
        f"## Ambiguity Lock Register\n\n"
        f"{alr_table}\n"
    )

    # 4. Verify Gate 1.7 passes cryptographically
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(
        output_01, metadata, ledger_path=ledger
    )
    assert passed is True
    assert details["hitl_receipt_bound"] is True
    assert details["verification_type"] == "ed25519_jcs"
    assert details["verified_entry_id"] == verified_entry_id
    assert failures == []


# ---------------------------------------------------------------------------
# 2. E2E SurgicalRemediator Structural Healing (<50ms, 0 LLM calls)
# ---------------------------------------------------------------------------

def test_surgical_remediator_gate1_structural_healing_e2e() -> None:
    """E2E lifecycle: heal multiple Gate 1 structural defects deterministically in <50ms with 0 LLM calls."""
    attendees = [
        {"name": "Alice Martin", "org": "PartnerAdvisory", "role": "Architect"},
        {"name": "Bob Vance", "org": "PartnerAdvisory", "role": "Engineer"},
        {"name": "Charlie Day", "org": "Client", "role": "PM"},
    ]
    metadata: Dict[str, Any] = {
        "meeting_name": "Tech_Roadmap_Review",
        "date": "09/30/2026",
        "attendees": attendees,
        "speaker_map": {"SPEAKER_00": "Alice Martin", "SPEAKER_01": "Bob Vance"},
    }

    # Defective draft: 2-part timestamps, unannotated 210s gap, wrong header, missing summary, outro hallucination
    raw_draft = (
        "# Live Meeting Transcript: Tech Roadmap Review\n\n"
        "Date: 2026-09-30\n\n"
        "### [01:00]\n\n"
        "**Alice Martin:** We should deploy the service.\n\n"
        "### [04:30]\n\n"
        "**Bob Vance:** Agreed on the service deployment.\n\n"
        "### [20:00]\n\n"
        "**Bob Vance:** Thank you for watching. Please subscribe.\n"
    )

    qa_report = {
        "output_01_forensic_transcript": {
            "failures": [
                "DATE_FORMAT_INVALID",
                "TIMESTAMP_SYNTAX_ERROR",
                "UNANNOTATED_RECORDING_GAP",
                "ATTRIBUTION_SUMMARY_MISSING",
                "AMBIGUITY_LOCK_REGISTER_MISSING",
            ]
        }
    }

    start_time = time.perf_counter()
    remediated, did_remediate, audit_trail = SurgicalRemediator.remediate_output_1(
        raw_draft,
        qa_report=qa_report,
        judge_report={},
        metadata=metadata,
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    # Verification: must execute in <50ms without external/LLM calls
    assert elapsed_ms < 50.0, f"Surgical remediation took {elapsed_ms:.2f}ms, expected <50ms"
    assert did_remediate is True

    # Check structural fixes
    assert "# Forensic Transcript" in remediated
    assert "**Date:** 09/30/2026" in remediated
    assert "[00:01:00]" in remediated
    assert "[00:04:30]" in remediated
    assert "[Recording gap: 00:01:00 – 00:04:30]" in remediated
    
    # Outro phrases purged from spoken dialogue, logged in ALR
    dialogue = remediated.split("## Ambiguity Lock Register")[0]
    assert "Thank you for watching" not in dialogue
    assert "Please subscribe" not in dialogue
    assert "## Attribution Summary" in remediated
    assert "## Ambiguity Lock Register" in remediated
    assert "ASR Outro Hallucination Artifacts" in remediated


# ---------------------------------------------------------------------------
# 3. E2E Anti-Forgery Preservation & Gate 1.7 Fail-Closed
# ---------------------------------------------------------------------------

def test_surgical_remediator_preserves_forged_claim_so_gate1_7_fails_closed(tmp_path: Path) -> None:
    """E2E lifecycle: draft with forged Operator Verified claim must NOT be masked by SurgicalRemediator; fails Gate 1.7."""
    ledger = _setup_test_ledger(tmp_path)
    metadata: Dict[str, Any] = {
        "meeting_name": "Forged_Claim_Audit",
        "meeting_slug": "forged_claim_audit",
        "date": "09/30/2026",
        "project": "nea",
        "attendees": ["Alice Martin", "Bob Vance"],
        "hitl_ledger_path": str(ledger),
    }

    draft_with_forgery = (
        "# Forensic Transcript\n\n"
        "**Date:** 09/30/2026\n\n"
        "### [00:01:00]\n\n"
        "**Alice Martin:** First statement.\n\n"
        "### [00:01:10]\n\n"
        "**Bob Vance:** Second statement.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution | [00:00:00] | stmt-01 | Alice Martin | Multi-Tier Acoustic Isolation Protocol (Operator Verified) |\n"
        "| 2 | Speaker Attribution | [00:00:00] | stmt-02 | Bob Vance | Acoustic Grounding |\n"
    )

    # Pass through SurgicalRemediator
    remediated, _, _ = SurgicalRemediator.remediate_output_1(
        draft_with_forgery,
        qa_report={"output_01_forensic_transcript": {"failures": ["outro_check"]}},
        judge_report={},
        metadata=metadata,
    )

    # Forged claim must survive remediation (anti-masking guarantee)
    assert "(Operator Verified)" in remediated

    # Gate 1.7 audit must FAIL CLOSED
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(
        remediated, metadata, ledger_path=ledger
    )
    assert passed is False
    assert details["hitl_receipt_bound"] is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures)


# ---------------------------------------------------------------------------
# 4. E2E Gate 1.10 Entity Context Scan on Output 01 Dialogue
# ---------------------------------------------------------------------------

def test_gate1_10_scans_output01_dialogue_for_ungrounded_stakeholders() -> None:
    """E2E verification: ungrounded third-party stakeholder in Output 01 dialogue is flagged by Gate 1.10."""
    class StubResolver:
        def get_first_names_requiring_grounding(self, attendees=None):
            return {"karen", "jordan"}

    stub = StubResolver()
    f1_dialogue_with_bare_name = (
        "# Forensic Transcript\n\n"
        "**Date:** 09/30/2026\n\n"
        "### [00:01:00]\n\n"
        "**Alice Martin:** We coordinated with Jordan on the infrastructure cutover.\n"
    )
    f2_clean = "## Executive Recap\n\nAll milestones on schedule."

    passed, failures = evaluate_referenced_entity_context_gate(
        f1_text=f1_dialogue_with_bare_name,
        f2_text=f2_clean,
        roster_names=["Alice Martin"],
        entity_clarifications=[],
        identity_resolver=stub,
    )
    assert passed is False
    assert any("C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT" in f for f in failures)
    assert any("Jordan" in f for f in failures)
