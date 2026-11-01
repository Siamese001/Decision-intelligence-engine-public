"""
tests/test_hitl_alr_ledger_replay_persistence_adversarial.py

Wave 5 Adversarial and Edge-Case Tests for Non-Expiring Replay Binding
and ALR Digest Stability.

Covers:
1. compute_alr_subject_digest canonicalization stability across date formats, project casing, and expanded roster names.
2. verify_alr_cryptographic_receipt (Gate 1.7) verifies signed approval entries >7 days old without expiration failure.
3. verify_collapse_approval_in_ledger honors signed collapse approvals >7 days old without TTL expiration.
4. _load_ledger_role_resolutions populates speaker_role_resolutions with provenance='HITL_LEDGER' and is_human_confirmed=True.
5. speaker_role_reconciler honors ledger confirmed roles and does not downgrade to ROSTER_PRIOR.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from datetime import datetime, timezone, timedelta
import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    get_governed_hitl_keypair,
    canonicalize_subject_dict,
)
from dual_pipeline.transcript_qa_core.gate1_alr import verify_alr_cryptographic_receipt
from dual_pipeline.speaker_attribution_core.collapse_detector import verify_collapse_approval_in_ledger
from dual_pipeline.canonical_pipeline.u0_ingress import _load_ledger_role_resolutions
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import reconcile_speaker_role_resolutions


def test_subject_digest_canonicalization_stability():
    """Verifies that dates (hyphen vs slash), project casing, and expanded roster names produce identical digests."""
    # Variant A: Standard ISO date, uppercase project, base names
    sub_a = {
        "meeting_name": "BCG Partner Discussion",
        "meeting_date": "2026-09-28",
        "project": "BCG",
        "attendees": ["Alex Rivera", "Peter", "Will Booth"],
    }

    # Variant B: Slash US date, lowercase project, parenthetical title expansion
    sub_b = {
        "meeting_name": "BCG Partner Discussion",
        "meeting_date": "09/28/2026",
        "project": "bcg",
        "attendees": ["Alex Rivera (Managing Director)", "Peter", "Will Booth (Partner)"],
    }

    # Variant C: Hyphen US date, mixed-case project, dict-style attendee entries
    sub_c = {
        "meeting_name": "BCG Partner Discussion",
        "meeting_date": "09-28-2026",
        "project": "Bcg",
        "attendees": [
            {"name": "Alex Rivera (Managing Director)"},
            {"name": "Peter"},
            {"name": "Will Booth"},
        ],
    }

    digest_a = compute_alr_subject_digest(sub_a)
    digest_b = compute_alr_subject_digest(sub_b)
    digest_c = compute_alr_subject_digest(sub_c)

    assert digest_a == digest_b, f"Digest A ({digest_a}) != Digest B ({digest_b})"
    assert digest_a == digest_c, f"Digest A ({digest_a}) != Digest C ({digest_c})"


def test_alr_gate1_7_verifies_historical_approval_over_7_days_old():
    """Verifies that Gate 1.7 accepts a cryptographically signed approval entry created >7 days ago."""
    priv, pub = get_governed_hitl_keypair()

    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger_path = Path(tmp_dir) / "hitl_decision_ledger.json"

        # Subject at signing time
        signing_subject = {
            "meeting_name": "BCG Leadership Alignment",
            "meeting_date": "2026-09-28",
            "project": "BCG",
            "attendees": ["Alex Rivera", "Peter"],
        }
        subject_sha = compute_alr_subject_digest(signing_subject)

        # Create signed entry with timestamps 10 days in the past (well past 24h TTL)
        past_time = datetime.now(timezone.utc) - timedelta(days=10)
        past_iso = past_time.isoformat().replace("+00:00", "Z")
        expired_time = past_time + timedelta(hours=24)
        expired_iso = expired_time.isoformat().replace("+00:00", "Z")

        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_sha,
            case_ids=["case_123"],
            decision="APPROVE",
            operator_id="human_operator",
            private_key=priv,
            public_key=pub,
        )
        # Override timestamps to simulate a historical entry
        entry["payload"]["issued_at"] = past_iso
        entry["payload"]["approved_at"] = past_iso
        entry["payload"]["expires_at"] = expired_iso
        from dual_pipeline.hitl_governance_core.crypto import sign_hitl_approval_payload
        entry["signature"] = sign_hitl_approval_payload(entry["payload"], priv)

        ledger_path.write_text(json.dumps([entry]), encoding="utf-8")

        # Now verify Gate 1.7 during replay (current time > 10 days later)
        meta = {
            "meeting_name": "BCG Leadership Alignment",
            "date": "09/28/2026",  # Different date format
            "project": "bcg",        # Different casing
            "attendees": ["Alex Rivera (Managing Director)", "Peter"],  # Expanded roster
            "hitl_ledger_path": str(ledger_path),
        }
        transcript_text = """### Ambiguity Lock Register
| Field | Value | Notes |
| Speaker Attribution | Alex Rivera | (Operator Verified) |

**[00:00:10] Alex Rivera:** Cross-group alignment confirmed."""

        passed, report, diagnostics = verify_alr_cryptographic_receipt(
            transcript_text, meta, ledger_path=ledger_path
        )
        assert passed is True, f"Gate 1.7 must pass historical verification: {diagnostics}"
        assert report.get("hitl_receipt_bound") is True
        assert report.get("verification_type") == "ed25519_jcs"
        assert len(report.get("claims_detected", [])) >= 1


def test_collapse_detector_verifies_historical_approval():
    """Verifies that collapse detector honors signed collapse approvals >7 days old."""
    priv, pub = get_governed_hitl_keypair()

    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger_path = Path(tmp_dir) / "hitl_decision_ledger.json"

        subject_dict = {
            "meeting_name": "Solo Briefing",
            "meeting_date": "2026-09-28",
            "project": "bcg",
            "attendees": ["Alex Rivera"],
        }
        subject_sha = compute_alr_subject_digest(subject_dict)

        past_time = datetime.now(timezone.utc) - timedelta(days=14)
        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_sha,
            case_ids=["SC-001", "single-speaker-collapse"],
            decision="APPROVE",
            operator_id="human_operator",
            private_key=priv,
            public_key=pub,
        )
        entry["payload"]["issued_at"] = past_time.isoformat()
        entry["payload"]["expires_at"] = (past_time + timedelta(hours=24)).isoformat()
        from dual_pipeline.hitl_governance_core.crypto import sign_hitl_approval_payload
        entry["signature"] = sign_hitl_approval_payload(entry["payload"], priv)

        ledger_path.write_text(json.dumps([entry]), encoding="utf-8")

        meta = {
            "subject": subject_dict,
            "hitl_ledger_path": str(ledger_path),
        }
        approved = verify_collapse_approval_in_ledger(meta, ledger_path=ledger_path)
        assert approved is True, "Must verify historical collapse approval without expiration failure"


def test_load_ledger_role_resolutions_populates_full_dicts():
    """Verifies that _load_ledger_role_resolutions populates provenance='HITL_LEDGER' and is_human_confirmed=True."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root_p = Path(tmp_dir)
        ledger_file = root_p / "hitl_decision_ledger.json"
        ledger_content = [
            {
                "payload": {
                    "metadata": {
                        "speaker_role_resolutions": {
                            "Alex Rivera": {
                                "role": "Managing Director & Senior Partner",
                                "provenance": "HITL_LEDGER",
                                "is_human_confirmed": True,
                            }
                        }
                    }
                }
            }
        ]
        ledger_file.write_text(json.dumps(ledger_content), encoding="utf-8")

        meta = {
            "repo_root": str(root_p),
            "attendees": ["Alex Rivera", "Peter"],
        }
        resolved = _load_ledger_role_resolutions(meta)
        assert "Alex Rivera" in resolved
        assert resolved["Alex Rivera"] == "Managing Director & Senior Partner"

        sp_resolutions = meta.get("speaker_role_resolutions", {})
        assert "Alex Rivera" in sp_resolutions
        assert sp_resolutions["Alex Rivera"]["provenance"] == "HITL_LEDGER"
        assert sp_resolutions["Alex Rivera"]["is_human_confirmed"] is True


def test_speaker_role_reconciler_preserves_ledger_confirmed_roles():
    """Verifies that speaker_role_reconciler preserves HITL ledger confirmed roles without downgrade."""
    meta = {
        "speaker_role_resolutions": {
            "Alex Rivera": {
                "role": "Managing Director & Senior Partner",
                "provenance": "HITL_LEDGER",
                "is_human_confirmed": True,
            }
        }
    }
    graph_data = {
        "entities": [
            {
                "id": "speaker:mitchell-simmons",
                "name": "Alex Rivera",
                "category": "PERSON",
                "professional_title": "Partner",  # Outdated or lower prior
                "provenance": "ROSTER_PRIOR",
            }
        ]
    }

    reconciled = reconcile_speaker_role_resolutions(graph_data, metadata=meta)
    entities = reconciled.get("entities", [])
    assert len(entities) == 1
    ent = entities[0]
    assert ent.get("professional_title") == "Managing Director & Senior Partner"
    assert ent.get("provenance") == "HITL_LEDGER"
    assert ent.get("is_human_confirmed") is True
