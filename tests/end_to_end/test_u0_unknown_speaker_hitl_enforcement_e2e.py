"""
tests/end_to_end/test_u0_unknown_speaker_hitl_enforcement_e2e.py

Wave 2 E2E & Adversarial Acceptance Suite:
1. Stage U0 Unknown Speaker HITL receipt binding enforcement.
2. Subject-digest and Ed25519 signature verification on attendee confirmation receipts.
3. Ingest worktree (ingest/*) dual_pipeline/ code-freeze protection.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    batch_validate_preflight_checks,
    raise_if_preflight_failed,
    UnifiedPreflightCascadeError,
)
from dual_pipeline.cli.ingest import assert_ingest_worktree_code_freeze
from dual_pipeline.hitl_governance_core.crypto import (
    create_signed_hitl_approval_entry,
    append_signed_approval_to_ledger,
    get_governed_hitl_keypair,
)
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass


def test_u0_unknown_speaker_blocked_without_hitl_receipt(tmp_path: Path):
    """
    RCA Scenario:
    Preflight must detect Unknown acoustic utterances and fail closed
    with U0_UNMAPPED_SPEAKERS unless covered by an authenticated HITL receipt.
    """
    raw_utterances = [
        {"speaker": "Eduardo Juan More", "text": "Turn 0", "start": 0.0, "end": 2.0},
        {"speaker": "Unknown", "text": "Turn 1 unassigned", "start": 2.0, "end": 4.0},
        {"speaker": "Alex Sanchez", "text": "Turn 2", "start": 4.0, "end": 6.0},
    ]

    meta = {
        "time": "02:00 PM - 03:00 PM ET",
        "date": "10-13-2026",
        "meeting_name": "Sync Mandy PartnerAdvisory",
        "attendees": ["Eduardo Juan More", "Alex Sanchez"],
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": "meeting_source_hash_abc123",
        },
        "source_file_hash": "meeting_source_hash_abc123",
        "structured_utterances": raw_utterances,
        "speaker_map": {
            "Eduardo Juan More": "Eduardo Juan More",
            "Alex Sanchez": "Alex Sanchez",
        },
    }

    dialogue = "[Eduardo Juan More]: Turn 0\n[Alex Sanchez]: Turn 2"

    report = batch_validate_preflight_checks(metadata=meta, raw_transcript=dialogue)
    assert not report.is_valid
    diag_checks = [d.check_name for d in report.diagnostics]
    assert "U0_UNMAPPED_SPEAKERS" in diag_checks
    assert "preflight_speaker_review_prompts" in meta
    prompts = meta["preflight_speaker_review_prompts"]
    assert len(prompts) == 1
    assert prompts[0]["turn_index"] == 1
    assert "TARGET" in prompts[0]["prompt"]
    assert "**UNRESOLVED — Unknown**" in prompts[0]["prompt"]

    from dual_pipeline.speaker_attribution_core.review_context_render import (
        validate_visible_speaker_context,
    )
    validate_visible_speaker_context(prompts[0]["prompt"])

    with pytest.raises(UnifiedPreflightCascadeError):
        raise_if_preflight_failed(report)


def test_u0_unknown_speaker_cleared_with_authenticated_ed25519_receipt(tmp_path: Path):
    """
    Verifies that when a valid Ed25519-signed HITL receipt covers the Unknown turn,
    Stage U0 preflight accepts the resolution and clears U0_UNMAPPED_SPEAKERS.
    """
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    subject_hash = "source_hash_valid_xyz789"

    priv_key, pub_key = get_governed_hitl_keypair(key_dir=tmp_path / "keys")
    signed_approval = create_signed_hitl_approval_entry(
        subject_sha256=subject_hash,
        case_ids=["1"], # Covers turn 1
        decision="RESOLVED_SPEAKER_HITL",
        operator_id="Amit Ayer",
        private_key=priv_key,
        public_key=pub_key,
    )
    append_signed_approval_to_ledger(signed_approval, ledger_path=ledger_file)

    raw_utterances = [
        {"speaker": "Eduardo Juan More", "text": "Turn 0", "start": 0.0, "end": 2.0},
        {"speaker": "Unknown", "text": "Turn 1 unassigned", "start": 2.0, "end": 4.0},
        {"speaker": "Alex Sanchez", "text": "Turn 2", "start": 4.0, "end": 6.0},
    ]

    meta = {
        "time": "02:00 PM - 03:00 PM ET",
        "date": "10-13-2026",
        "meeting_name": "Sync Mandy PartnerAdvisory",
        "attendees": ["Eduardo Juan More", "Alex Sanchez"],
        "attendees_confirmed_by_operator": True,
        "attendee_roles_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": subject_hash,
        },
        "source_file_hash": subject_hash,
        "structured_utterances": raw_utterances,
        "speaker_map": {
            "Eduardo Juan More": "Eduardo Juan More",
            "Alex Sanchez": "Alex Sanchez",
        },
    }

    dialogue = "[Eduardo Juan More]: Turn 0\n[Alex Sanchez]: Turn 2"

    with patch("dual_pipeline.hitl_governance_core.crypto.find_signed_approvals", return_value=[signed_approval]):
        report = batch_validate_preflight_checks(metadata=meta, raw_transcript=dialogue)
        assert report.is_valid
        assert "U0_UNMAPPED_SPEAKERS" not in [d.check_name for d in report.diagnostics]


def test_hitl_guard_rejects_attendee_confirmation_mismatched_subject():
    """
    RCA Scenario:
    Agent reused attendee_confirmation_receipt from a different run with subject_sha256 'ffa9db93...'
    while the active run input_hash was 'e49dce17...'.
    Must fail closed with ValueError.
    """
    meta = {
        "source_file_hash": "e49dce179a613589b2b2b1a8f6d531cf",
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": "ffa9db9312345678abcdef0123456789", # Stale / mismatched!
        },
    }

    with pytest.raises(ValueError, match="does not match active source hash"):
        assert_no_u0_hitl_bypass(meta)


def test_hitl_guard_rejects_missing_attendee_receipt_subject():
    """Verifies that an attendee confirmation receipt without subject_sha256 fails closed."""
    meta = {
        "source_file_hash": "e49dce179a613589b2b2b1a8f6d531cf",
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            # Missing subject_sha256!
        },
    }

    with pytest.raises(ValueError, match="missing subject_sha256 cryptographic binding"):
        assert_no_u0_hitl_bypass(meta)


def test_hitl_guard_rejects_invalid_signature_on_receipt(tmp_path: Path):
    """Verifies that an attendee confirmation receipt with a forged or tampered signature is rejected."""
    priv_key, pub_key = get_governed_hitl_keypair(key_dir=tmp_path / "keys")
    valid_approval = create_signed_hitl_approval_entry(
        subject_sha256="valid_source_sha256_hash",
        case_ids=["attendees"],
        decision="CONFIRM_ATTENDEES",
        operator_id="Amit Ayer",
        private_key=priv_key,
        public_key=pub_key,
    )

    # Tamper with the signature
    tampered_approval = dict(valid_approval)
    tampered_approval["signature"] = "BAD_SIGNATURE_BYTES"

    meta = {
        "source_file_hash": "valid_source_sha256_hash",
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": "valid_source_sha256_hash",
            "payload": tampered_approval["payload"],
            "signature": tampered_approval["signature"],
            "public_key_pem": tampered_approval["public_key_pem"],
        },
    }

    with pytest.raises(ValueError, match="Ed25519 signature verification failed"):
        assert_no_u0_hitl_bypass(meta)


def test_ingest_worktree_code_freeze_blocks_dirty_dual_pipeline(tmp_path: Path):
    """
    RCA Scenario:
    In ingest/* worktrees, agents are strictly code-frozen and cannot modify dual_pipeline/
    source files to game downstream QA gates.
    """
    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="ingest/sync-mandy-partner_advisory"), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", return_value=" M dual_pipeline/transcript_qa_core/graph_healer.py\n M dual_pipeline/canonical_pipeline/state_machine.py"):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: Ingestion worktree 'ingest/sync-mandy-partner_advisory' has dirty dual_pipeline/ source code modifications"):
            assert_ingest_worktree_code_freeze(worktree_dir=tmp_path)


def test_ingest_worktree_code_freeze_allows_clean_worktree(tmp_path: Path):
    """Verifies that an ingest/* worktree with clean dual_pipeline/ passes without error."""
    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="ingest/sync-mandy-partner_advisory"), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", return_value=""):
        # Should not raise
        assert_ingest_worktree_code_freeze(worktree_dir=tmp_path)


def test_ingest_worktree_code_freeze_allows_feature_branches(tmp_path: Path):
    """Verifies that non-ingest branches (e.g. feat/*) are exempt from the ingest code-freeze."""
    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/rca-eduardo-attribution-bypass"), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", return_value=" M dual_pipeline/cli/ingest.py"):
        # Feature branches are allowed to modify engine code
        assert_ingest_worktree_code_freeze(worktree_dir=tmp_path)
