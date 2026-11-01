"""Adversarial and edge-case test suite for Wave 8: Speaker hints, conflicts, and cryptographic invariants."""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.crypto import (
    get_governed_hitl_keypair,
    verify_hitl_approval_signature,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hints import (
    SpeakerHintManager,
)


class TestSpeakerHintConflictsAdversarial(unittest.TestCase):
    """Adversarial test suite covering quote-matching edge cases, conflict resolution, and anti-bypass invariants."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_w8_adv_")
        self.tmp_path = Path(self.temp_dir)
        self.output_dir = self.tmp_path / "output"
        self.cache_dir = self.tmp_path / "cache"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"

        self.segments_file = self.cache_dir / "segments.jsonl"
        self.transcript_md = self.output_dir / "transcript.md"

        self.roster = ["Samantha Vance", "Alex Mercer", "Marcus Vance"]
        self.manager = SpeakerHintManager(
            output_dir=self.output_dir,
            cache_dir=self.cache_dir,
            attendees=self.roster,
            ledger_path=self.ledger_path,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_segments(self, segs: list[dict]) -> None:
        lines = [json.dumps(s) for s in segs]
        self.segments_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_quote_not_found_fails_closed(self) -> None:
        """Adversarial: Quote absent from all segments fails closed with zero mutations."""
        initial_segs = [
            {"chunk_idx": 0, "text": "Good morning team", "voice_id": "Voice A", "speaker": "Voice A"},
            {"chunk_idx": 1, "text": "Ready to discuss architecture", "voice_id": "Voice B", "speaker": "Voice B"},
        ]
        self._write_segments(initial_segs)

        res = self.manager.apply_hint(
            quote="completely non-existent phrase in this meeting",
            speaker_name="Samantha Vance",
            segments_jsonl_path=self.segments_file,
            transcript_md_path=self.transcript_md,
        )

        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.error, "QUOTE_NOT_FOUND")
        self.assertFalse(self.ledger_path.is_file(), "Ledger should not be written on failure")

        # Verify segments file remains unmodified
        lines = self.segments_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        s0 = json.loads(lines[0])
        self.assertEqual(s0["speaker"], "Voice A")
        self.assertNotIn("hint_applied", s0)

    def test_ambiguous_quote_matches_multiple_distinct_voices(self) -> None:
        """Adversarial: Quote present in turns from distinct voices fails closed to prevent incorrect attribution."""
        initial_segs = [
            {"chunk_idx": 0, "text": "I agree with the architecture proposal", "voice_id": "Voice A", "speaker": "Voice A"},
            {"chunk_idx": 1, "text": "I agree with the architecture proposal completely", "voice_id": "Voice B", "speaker": "Voice B"},
        ]
        self._write_segments(initial_segs)

        res = self.manager.apply_hint(
            quote="agree with the architecture proposal",
            speaker_name="Samantha Vance",
            segments_jsonl_path=self.segments_file,
            transcript_md_path=self.transcript_md,
        )

        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.error, "AMBIGUOUS_QUOTE_MATCH")
        self.assertIn("multiple distinct voices", res.details.lower())

        # Verify zero segments were modified
        lines = self.segments_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(json.loads(lines[0])["speaker"], "Voice A")
        self.assertEqual(json.loads(lines[1])["speaker"], "Voice B")

    def test_non_roster_speaker_rejected_without_force(self) -> None:
        """Adversarial: Assigning non-roster speaker fails closed unless force=True is explicitly supplied."""
        initial_segs = [
            {"chunk_idx": 0, "text": "Let us review the security audit findings", "voice_id": "Voice A", "speaker": "Voice A"},
        ]
        self._write_segments(initial_segs)

        # Attempt assignment with uninvited/unrostered speaker
        res = self.manager.apply_hint(
            quote="security audit findings",
            speaker_name="Unknown Uninvited Guest",
            segments_jsonl_path=self.segments_file,
            force=False,
        )
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.error, "NON_ROSTER_SPEAKER")

        # Now pass force=True to authorize out-of-band attendee
        res_forced = self.manager.apply_hint(
            quote="security audit findings",
            speaker_name="Unknown Uninvited Guest",
            segments_jsonl_path=self.segments_file,
            force=True,
        )
        self.assertEqual(res_forced.status, "PASS")
        updated = json.loads(self.segments_file.read_text(encoding="utf-8").strip())
        self.assertEqual(updated["speaker"], "Unknown Uninvited Guest")
        self.assertTrue(updated.get("hint_applied"))

    def test_conflicting_hint_operator_always_wins_and_logs_conflict(self) -> None:
        """Adversarial: Hint overrides prior pyannote attribution; conflict logged to speaker_hint_conflicts.jsonl."""
        initial_segs = [
            {"chunk_idx": 0, "text": "Initial statement by speaker", "voice_id": "Voice A", "speaker": "Alex Mercer"},
            {"chunk_idx": 1, "text": "Continuing statement by speaker", "voice_id": "Voice A", "speaker": "Alex Mercer"},
        ]
        self._write_segments(initial_segs)

        conflicts_file = self.output_dir / "speaker_hint_conflicts.jsonl"
        self.assertFalse(conflicts_file.exists())

        # Operator overrides: Voice A was actually Samantha Vance, not Alex Mercer
        res = self.manager.apply_hint(
            quote="Initial statement by speaker",
            speaker_name="Samantha Vance",
            segments_jsonl_path=self.segments_file,
            transcript_md_path=self.transcript_md,
            meeting_slug="conflict_test",
        )
        self.assertEqual(res.status, "PASS")
        self.assertGreaterEqual(res.conflicts_logged, 1)

        # Conflict file must record the disagreement
        self.assertTrue(conflicts_file.is_file())
        conflict_lines = conflicts_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(conflict_lines), 1)
        conflict_data = json.loads(conflict_lines[0])
        self.assertEqual(conflict_data["voice_id"], "Voice A")
        self.assertEqual(conflict_data["prior_speaker"], "Alex Mercer")
        self.assertEqual(conflict_data["hinted_speaker"], "Samantha Vance")
        self.assertEqual(conflict_data["resolution"], "OPERATOR_HINT_WINS")

        # Invariant: Operator hint always wins in segments.jsonl
        lines = self.segments_file.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            s = json.loads(line)
            self.assertEqual(s["speaker"], "Samantha Vance")
            self.assertTrue(s["hint_applied"])

    def test_malformed_and_boundary_payloads(self) -> None:
        """Boundary: Handles empty quotes, missing files, corrupted JSONL gracefully."""
        # 1. Missing segments file
        res1 = self.manager.apply_hint(
            quote="testing",
            speaker_name="Samantha Vance",
            segments_jsonl_path=self.tmp_path / "does_not_exist.jsonl",
        )
        self.assertEqual(res1.status, "FAIL")
        self.assertEqual(res1.error, "SEGMENTS_FILE_NOT_FOUND")

        # 2. Corrupted JSON lines in segments file
        corrupted_file = self.cache_dir / "corrupted_segments.jsonl"
        corrupted_file.write_text("NOT_VALID_JSON\n{\"chunk_idx\": 0}\n", encoding="utf-8")
        res2 = self.manager.apply_hint(
            quote="testing",
            speaker_name="Samantha Vance",
            segments_jsonl_path=corrupted_file,
        )
        # Should fail cleanly without raising uncaught exception
        self.assertEqual(res2.status, "FAIL")

        # 3. Empty quote
        res3 = self.manager.apply_hint(
            quote="",
            speaker_name="Samantha Vance",
            segments_jsonl_path=self.segments_file,
        )
        self.assertEqual(res3.status, "FAIL")

    def test_cryptographic_tamper_detection_in_ledger(self) -> None:
        """Security: Tampering with payload or signature in hitl_decision_ledger.json fails verification."""
        initial_segs = [
            {"chunk_idx": 0, "text": "Authoritative governance statement", "voice_id": "Voice A", "speaker": "Voice A"},
        ]
        self._write_segments(initial_segs)

        res = self.manager.apply_hint(
            quote="governance statement",
            speaker_name="Samantha Vance",
            segments_jsonl_path=self.segments_file,
            operator_id="security_officer",
            meeting_slug="tamper_test",
        )
        self.assertEqual(res.status, "PASS")

        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        entry = data["decisions"][-1]
        _, pub_key = get_governed_hitl_keypair()

        # Legitimate verification passes
        valid = verify_hitl_approval_signature(
            payload=entry["payload"],
            signature_b64=entry["signature"],
            public_key=pub_key,
        )
        self.assertTrue(valid)

        # Adversarial tamper 1: Alter speaker in payload
        tampered_payload = dict(entry["payload"])
        tampered_payload["metadata"] = dict(tampered_payload["metadata"])
        tampered_payload["metadata"]["speaker"] = "Attacker Malicious"
        tampered_valid = verify_hitl_approval_signature(
            payload=tampered_payload,
            signature_b64=entry["signature"],
            public_key=pub_key,
        )
        self.assertFalse(tampered_valid, "Tampered payload must fail signature verification")

        # Adversarial tamper 2: Corrupt signature bytes
        corrupted_sig = entry["signature"][:-4] + "AAAA"
        corrupted_valid = verify_hitl_approval_signature(
            payload=entry["payload"],
            signature_b64=corrupted_sig,
            public_key=pub_key,
        )
        self.assertFalse(corrupted_valid, "Corrupted signature must fail verification")

    def test_concurrency_race_condition_safe_under_lock(self) -> None:
        """Concurrency: Multiple threads applying hints concurrently preserve ledger integrity and lock safety."""
        # 10 segments belonging to 10 distinct voices
        initial_segs = [
            {"chunk_idx": i, "text": f"Distinct statement number {i}", "voice_id": f"Voice {chr(65+i)}", "speaker": f"Voice {chr(65+i)}"}
            for i in range(5)
        ]
        self._write_segments(initial_segs)

        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                # Each thread applies a hint for its segment
                self.manager.apply_hint(
                    quote=f"statement number {idx}",
                    speaker_name="Samantha Vance",
                    segments_jsonl_path=self.segments_file,
                    force=True,
                    meeting_slug="concurrent_test",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Concurrent workers encountered errors: {errors}")

        # Ledger must remain parseable and valid JSON
        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        decisions = data.get("decisions", [])
        self.assertEqual(len(decisions), 5)


if __name__ == "__main__":
    unittest.main()
