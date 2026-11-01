#!/usr/bin/env python3
"""End-to-End Test Suite for Single-Speaker Collapse Interception & Cryptographic HITL Resolution.

Exercises the complete lifecycle from RCA on Alan_Q4_Scope_Hayden_Amit:
1. Intake of multi-attendee meeting where turn distribution collapses (74/74 turns = 100% skew).
2. Pre-Gate 1 deterministic interception: AudioSpeakerAttributionEngine raises HITLRequiredError.
3. Subprocess CLI layer halts with Exit Code 2 emitting RFC schema-valid JSON packet.
4. Operator resolution: signed Ed25519 approval entry appended to hitl_decision_ledger.json.
5. Verification & Clearance: Gate 1 QA independently validates Ed25519 receipt and clears Gate 1.7.
6. Adversarial integrity: fabricated (Operator Verified) without cryptographic backing fails closed.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
    HITLRequiredError,
)
from dual_pipeline.transcript_cli import main as cli_main
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
    verify_alr_cryptographic_receipt,
)


def _make_transcript_with_alr(
    speakers: list[str],
    alr_resolution_basis: str = "Multi-Tier Acoustic Protocol (Operator Verified)",
    num_turns: int = 12,
) -> str:
    """Generate a valid transcript structure with customized ALR resolution basis."""
    header = (
        "# Executive Session -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/25/2026\n"
        "**Participants:** " + ", ".join(f"{s} (PartnerAdvisory)" for s in speakers) + "\n\n---\n\n"
    )
    turns = ""
    for i in range(num_turns):
        spk = speakers[i % len(speakers)]
        turns += f"### [00:{i:02d}:00]\n\n**{spk}:** Discussion turn {i} concerning architecture.\n\n"

    alr = (
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
    )
    for j, spk in enumerate(speakers):
        alr += f"| {j+1} | Speaker Attribution: {spk} | [00:00:00 - Session End] | stmt-{j} | Attributed to {spk} | {alr_resolution_basis} |\n"
    alr += "| 3 | Technical Lexicon | [00:01:00] | stmt-lex | Calibrated term | Lexicon Match |\n"

    summary = (
        "\n## Attribution Summary\n\n"
        "| Speaker | Org | Role | Turns Attributed | Confidence Tier | Primary Anchor Cue |\n"
        "|---|---|---|---|---|---|\n"
    )
    for spk in speakers:
        summary += f"| {spk} | PartnerAdvisory | Architect | 6 | HIGH | Voice signature |\n"

    return header + turns + alr + summary


@pytest.mark.e2e
class HitlAudioSpeakerCollapseE2ETests(unittest.TestCase):
    """End-to-end lifecycle verification of single-speaker collapse defense."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"
        envelope = {
            "schema_version": "1.0.0",
            "record_count": 0,
            "updated_at": "2026-09-25T12:00:00Z",
            "genesis_hash": "0" * 64,
            "latest_entry_hash": "0" * 64,
            "decisions": [],
        }
        self.ledger_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")

        self.priv_key, self.pub_key = generate_hitl_keypair()
        self.speakers = ["Hayden Barnes", "Amit Ayer"]
        self.roster = [
            {"name": "Hayden Barnes", "org": "PartnerAdvisory", "role": "Lead"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Architect"},
        ]

        # Build 74-turn collapsed transcript mimicking the Alan meeting RCA
        turns = []
        for i in range(1, 75):
            minute = (i * 20) // 60
            second = (i * 20) % 60
            ts = f"{minute:02d}:{second:02d}"
            turns.append(f"[{ts}] **Hayden:** Speech segment {i} discussing Alan deliverables and scope.")
        self.collapsed_body = "\n".join(turns)
        self.metadata = {
            "project": "nea",
            "meeting_name": "Alan_Q4_Scope_Hayden_Amit",
            "date": "09/25/2026",
            "platform": "Google Meet",
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Architect)"],
                "Apex Global Enterprises": ["Hayden Barnes (Lead)"],
            },
            "hitl_ledger_path": str(self.ledger_path),
            "diarization_status": "UNDIARIZED",
            "time": "09:00 AM - 10:00 AM ET",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_step1_speaker_attribution_engine_intercepts_collapse(self) -> None:
        """Step 1: Attribution engine detects 74/74 skew (100% > 85%) and raises HITLRequiredError."""
        engine = AudioSpeakerAttributionEngine()
        with self.assertRaises(HITLRequiredError) as ctx:
            engine.analyze_and_resolve(
                self.collapsed_body,
                self.metadata,
            )
        err = ctx.exception
        self.assertEqual(err.hitl_type, "single_speaker_collapse")
        self.assertEqual(err.evidence.get("dominant_label"), "Hayden")
        self.assertEqual(err.evidence.get("total_turns"), 74)
        self.assertEqual(err.evidence.get("dominant_label_turns"), 74)

    def test_e2e_step2_cli_halts_with_exit_code_2_and_schema_packet(self) -> None:
        """Step 2: transcript_cli returns Exit Code 2 and emits JSON packet to stdout."""
        input_file = self.tmp_path / "collapsed_meeting.txt"
        input_file.write_text(self.collapsed_body, encoding="utf-8")
        out_dir = self.tmp_path / "output"
        meta_file = self.tmp_path / "metadata.json"
        meta_file.write_text(json.dumps(self.metadata), encoding="utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        test_args = [
            "transcript_cli.py",
            "--input", str(input_file),
            "--output-dir", str(out_dir),
            "--metadata", str(meta_file),
            "--meeting-name", "Alan_Q4_Scope_Hayden_Amit",
            "--date", "09/25/2026",
        ]

        with patch("sys.argv", test_args), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            exit_code = cli_main()

        self.assertEqual(exit_code, 2, "Must return Exit Code 2 for HITL_REQUIRED")
        out_str = stdout_buf.getvalue().strip()
        self.assertTrue(out_str, "Stdout must contain machine-readable JSON packet")

        packet = json.loads(out_str)
        self.assertEqual(packet.get("protocol"), "dual_pipeline.cli.v1")
        self.assertEqual(packet.get("status"), "hitl_required")
        self.assertEqual(packet.get("reason"), "single_speaker_collapse")
        self.assertEqual(packet.get("evidence", {}).get("dominant_label"), "Hayden")
        self.assertEqual(packet.get("action"), "ask_user_question")

    def test_e2e_step3_operator_signs_approval_into_ledger(self) -> None:
        """Step 3: Operator resolves collapse and signs Ed25519 approval entry into ledger."""
        subject = {
            "meeting_id": "Alan_Q4_Scope_Hayden_Amit_09-25-2026",
            "meeting_name": "Alan_Q4_Scope_Hayden_Amit",
            "decision_category": "speaker_attribution_collapse",
            "dominant_speaker": "Hayden",
            "resolution": "partition_50_50",
            "operator_identity": "operator@governed.ai",
        }
        subject_digest = compute_alr_subject_digest(subject)

        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_digest,
            case_ids=["ALAN-COLLAPSE-01"],
            operator_id="operator@governed.ai",
            private_key=self.priv_key,
            public_key=self.pub_key,
        )
        self.assertEqual(entry["subject_sha256"], subject_digest)
        self.assertTrue(entry.get("signature"))

        append_signed_approval_to_ledger(entry, ledger_path=self.ledger_path)
        self.assertTrue(self.ledger_path.exists())

        f1_text = _make_transcript_with_alr(
            self.speakers,
            alr_resolution_basis="(Operator Verified)",
            num_turns=12,
        )
        meta = {
            "roster": self.roster,
            "subject_sha256": subject_digest,
            "hitl_ledger_path": str(self.ledger_path),
        }

        # Verify cryptographic verification helper confirms receipt
        passed, details, failures = verify_alr_cryptographic_receipt(
            f1_text,
            meta,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(passed, f"Receipt must be cryptographically bound: {failures}")
        self.assertTrue(details["hitl_receipt_bound"])
        self.assertEqual(details["operator_id"], "operator@governed.ai")

    def test_e2e_step4_resumed_synthesis_passes_gate1_with_valid_receipt(self) -> None:
        """Step 4: Output 01 with valid cryptographic receipt passes Gate 1 QA."""
        sub_hash = "alan_e2e_hash_" + "0" * 50
        entry = create_signed_hitl_approval_entry(
            subject_sha256=sub_hash,
            case_ids=["ALAN-COLLAPSE-01"],
            operator_id="operator@governed.ai",
            private_key=self.priv_key,
            public_key=self.pub_key,
        )
        append_signed_approval_to_ledger(entry, ledger_path=self.ledger_path)

        f1_text = _make_transcript_with_alr(
            self.speakers,
            alr_resolution_basis="(Operator Verified)",
            num_turns=12,
        )
        meta = {
            "roster": self.roster,
            "subject_sha256": sub_hash,
            "hitl_ledger_path": str(self.ledger_path),
        }

        # Gate 1.7 verification
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(
            f1_text, meta, ledger_path=self.ledger_path
        )
        self.assertTrue(passed, f"Gate 1.7 must pass: {failures}")
        self.assertTrue(details.get("hitl_receipt_bound"))
        self.assertEqual(details["crypto_verification"]["operator_id"], "operator@governed.ai")

        # Full run_qa_gate_1 integration
        qa_res = run_qa_gate_1(f1_text, metadata=meta)
        g1_7 = qa_res["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]
        self.assertTrue(g1_7["passed"])
        self.assertTrue(g1_7["hitl_receipt_bound"])

    def test_e2e_step5_unauthenticated_operator_claim_fails_gate1_closed(self) -> None:
        """Step 5: Output 01 with unauthenticated (Operator Verified) fails Gate 1.7 closed."""
        f1_text = _make_transcript_with_alr(
            self.speakers,
            alr_resolution_basis="(Operator Verified)",
            num_turns=12,
        )
        meta = {
            "roster": self.roster,
            "subject_sha256": "unauthenticated_hash_" + "0" * 43,
            "hitl_ledger_path": str(self.ledger_path),
        }

        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(
            f1_text, meta, ledger_path=self.ledger_path
        )
        self.assertFalse(passed, "Gate 1.7 must fail on unauthenticated claim")
        self.assertFalse(details.get("hitl_receipt_bound"))
        self.assertTrue(any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures))

        qa_res = run_qa_gate_1(f1_text, metadata=meta)
        g1_7 = qa_res["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]
        self.assertFalse(g1_7["passed"])
        self.assertFalse(g1_7["hitl_receipt_bound"])


if __name__ == "__main__":
    unittest.main()
