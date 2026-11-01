"""
Adversarial and edge-case tests for Wave 4: U0 Ingress Gate Parity & Collapse Detector Provenance Hardening.
Covers:
1. Invariant bypass attempt (substring 'hitl_resolved_transcript' without manifest/seal does not bypass C05/attendee checks)
2. Malformed / unverified container provenance (non-existent record_start_receipt_path under strict_u0_provenance rejected)
3. Exact production regression (expired TTL receipt rejected even when resume=True; modified transcript turns invalidate pre-seeded subject_sha256)
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.u0_ingress import (
    _has_authentic_live_container_proof,
    _is_notes_or_doc_exempt,
    batch_validate_preflight_checks,
)
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.speaker_attribution_core.collapse_detector import (
    _extract_expected_subject_digest,
    check_speaker_skew_and_collapse,
    extract_turn_speaker_inventory,
)


class TestU0IngressAndCollapseAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"
        self.ledger_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "record_count": 0,
                    "updated_at": "2026-10-28T12:00:00Z",
                    "genesis_hash": "0" * 64,
                    "latest_entry_hash": "0" * 64,
                    "decisions": [],
                }
            ),
            encoding="utf-8",
        )
        self.priv_key, self.pub_key = generate_hitl_keypair()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_unverified_hitl_resolved_transcript_filename_does_not_exempt_u0(self) -> None:
        # Category: Invariant bypass attempt
        fake_path = self.tmp_path / "01_fake_hitl_resolved_transcript.md"
        fake_path.write_text("[00:00:01] MysterySingleToken: Hello.", encoding="utf-8")

        meta = {
            "input_path": str(fake_path),
            "date": "10/28/2026",
            "time": "10:00 AM - 10:30 AM ET",
            "attendees": ["MysterySingleToken"],
        }
        self.assertFalse(_is_notes_or_doc_exempt(meta))
        rep = batch_validate_preflight_checks(meta, raw_transcript=fake_path.read_text(encoding="utf-8"))
        self.assertIn("attendee_confirmation", rep.missing_fields)

    def test_adversarial_forged_container_dir_without_receipt_blocked_under_strict_provenance(self) -> None:
        # Category: Malformed / unverified input
        empty_container = self.tmp_path / "empty_container"
        empty_container.mkdir(parents=True, exist_ok=True)
        meta = {
            "is_audio": True,
            "date": "10/28/2026",
            "time": "10:00 AM - 10:30 AM ET",
            "attendees": ["Alice Smith (Director, PartnerAdvisory)", "Bob Jones (VP, Apex Global)"],
            "container_dir": str(empty_container),
            "strict_u0_provenance": True,
            "attendee_roles_confirmed_by_operator": True,
        }
        self.assertFalse(_has_authentic_live_container_proof(meta))
        rep = batch_validate_preflight_checks(meta, raw_transcript="[00:00:05] Alice Smith: Hello.")
        self.assertIn("attendee_confirmation", rep.missing_fields)

    def test_adversarial_expired_ttl_rejected_even_on_resume_and_modified_turns_change_digest(self) -> None:
        # Category: Exact production regression & TOCTOU replay
        turns_v1 = "\n".join(f"Alice Smith: Original turn {i}." for i in range(12))
        turns_v2 = "\n".join(f"Alice Smith: Tampered turn {i} after approval." for i in range(12))
        roster = ["Alice Smith", "Bob Jones"]

        meta = {
            "meeting_name": "Replay Target",
            "date": "10/28/2026",
            "project": "nea",
            "roster": roster,
            "bind_transcript_turns_to_subject": True,
            "hitl_ledger_path": str(self.ledger_path),
            "resume": True,
        }
        subj_v1 = _extract_expected_subject_digest(meta, turns=extract_turn_speaker_inventory(turns_v1))

        # 1. Expired receipt must fail even when resume=True
        expired_entry = create_signed_hitl_approval_entry(
            subject_sha256=str(subj_v1),
            case_ids=["COLLAPSE-EXPIRED-RESUME"],
            operator_id="operator@governed.ai",
            private_key=self.priv_key,
            public_key=self.pub_key,
            ttl_seconds=-3600,
        )
        append_signed_approval_to_ledger(expired_entry, ledger_path=self.ledger_path)

        with self.assertRaises(HITLRequiredError):
            check_speaker_skew_and_collapse(turns_v1, roster, meta, ledger_path=self.ledger_path)

        # 2. Valid non-expired receipt for v1 turns must NOT authorize tampered v2 turns
        valid_entry = create_signed_hitl_approval_entry(
            subject_sha256=str(subj_v1),
            case_ids=["COLLAPSE-V1-VALID"],
            operator_id="operator@governed.ai",
            private_key=self.priv_key,
            public_key=self.pub_key,
            ttl_seconds=3600,
        )
        append_signed_approval_to_ledger(valid_entry, ledger_path=self.ledger_path)

        # Pre-seed metadata["subject_sha256"] = subj_v1 and attempt to run against turns_v2
        meta["subject_sha256"] = str(subj_v1)
        with self.assertRaises(HITLRequiredError):
            check_speaker_skew_and_collapse(turns_v2, roster, meta, ledger_path=self.ledger_path)


if __name__ == "__main__":
    unittest.main()
