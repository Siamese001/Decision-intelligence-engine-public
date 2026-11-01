"""
End-to-end verification for Wave 4: U0 Ingress Gate Parity & Collapse Detector Provenance Hardening.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.u0_ingress import batch_validate_preflight_checks
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


class TestU0IngressAndCollapseE2E(unittest.TestCase):
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

    def test_e2e_u0_ingress_enforces_mandatory_time_and_authentic_container_receipt(self) -> None:
        c_dir = self.tmp_path / "container"
        c_dir.mkdir(parents=True, exist_ok=True)
        (c_dir / "record_start_receipt.json").write_text(
            json.dumps({"session_id": "sess_1", "status": "RECORDING"}),
            encoding="utf-8",
        )

        # Missing time on audio/transcript fails U0_MANDATORY_TIME
        meta_no_time = {
            "is_audio": True,
            "date": "10/28/2026",
            "attendees": ["Alice Smith (Director, PartnerAdvisory)", "Bob Jones (VP, Apex Global)"],
            "container_dir": str(c_dir),
            "attendee_roles_confirmed_by_operator": True,
        }
        rep_no_time = batch_validate_preflight_checks(meta_no_time, raw_transcript="[00:00:05] Alice Smith: Hello.")
        self.assertIn("time", rep_no_time.missing_fields)

        # With time and authentic container receipt, passes
        meta_valid = dict(meta_no_time, time="10:00 AM - 10:30 AM ET")
        rep_valid = batch_validate_preflight_checks(meta_valid, raw_transcript="[00:00:05] Alice Smith: Hello.")
        self.assertTrue(rep_valid.is_valid)
        self.assertTrue(meta_valid.get("attendees_confirmed_by_operator"))

    def test_e2e_collapse_detector_clears_with_valid_non_expired_turn_bound_receipt(self) -> None:
        turns_text = "\n".join(f"Alice Smith: Turn {i} about platform stability." for i in range(12))
        turns = extract_turn_speaker_inventory(turns_text)
        roster = ["Alice Smith", "Bob Jones"]
        meta = {
            "meeting_name": "Stability Sync",
            "date": "10/28/2026",
            "project": "nea",
            "roster": roster,
            "bind_transcript_turns_to_subject": True,
            "hitl_ledger_path": str(self.ledger_path),
            "resume": True,
        }
        subj = _extract_expected_subject_digest(meta, turns=turns)
        self.assertIsNotNone(subj)

        entry = create_signed_hitl_approval_entry(
            subject_sha256=str(subj),
            case_ids=["COLLAPSE-VALID-01"],
            operator_id="operator@governed.ai",
            private_key=self.priv_key,
            public_key=self.pub_key,
            ttl_seconds=3600,
        )
        append_signed_approval_to_ledger(entry, ledger_path=self.ledger_path)

        res = check_speaker_skew_and_collapse(turns_text, roster, meta, ledger_path=self.ledger_path)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
