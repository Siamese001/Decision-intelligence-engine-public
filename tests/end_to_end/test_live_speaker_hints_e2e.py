"""End-to-End test suite for Wave 8: Live speaker identification and operator speaker hints."""
from __future__ import annotations

import contextlib
import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.hitl_governance_core.crypto import (
    get_governed_hitl_keypair,
    verify_hitl_approval_signature,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
    LiveVoiceRegistry,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hints import (
    SpeakerHintManager,
)
from dual_pipeline.transcript_pipeline_core.speaker_map_loader import (
    load_colocated_speaker_map,
)


def _generate_tone_wav(
    path: Path,
    freq: float,
    duration_sec: float = 3.0,
    sample_rate: int = 16000,
    amplitude: float = 12000.0,
) -> Path:
    """Generates a pure tone WAV file for acoustic signature verification."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = [
            int(amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate))
            for i in range(n_samples)
        ]
        w.writeframes(struct.pack(f"<{n_samples}h", *samples))
    return path


class TestLiveSpeakerHintsE2E(unittest.TestCase):
    """E2E test suite for live speaker identification, pyannote voice clustering, and operator hints."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_w8_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"
        self._recorders: list[Any] = []

    def tearDown(self) -> None:
        for rec in self._recorders:
            with contextlib.suppress(Exception):
                rec.stop()
            with contextlib.suppress(Exception):
                rec._release_slug_lock()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_voice_clustering_separation_and_centroid_update(self) -> None:
        """Verify LiveVoiceRegistry clusters distinct tones into distinct voices with high intra-voice stability."""
        wav_voice_1a = _generate_tone_wav(self.tmp_path / "v1a.wav", freq=150.0)
        wav_voice_1b = _generate_tone_wav(self.tmp_path / "v1b.wav", freq=155.0)
        wav_voice_2a = _generate_tone_wav(self.tmp_path / "v2a.wav", freq=350.0)
        wav_voice_2b = _generate_tone_wav(self.tmp_path / "v2b.wav", freq=355.0)

        registry = LiveVoiceRegistry(similarity_threshold=0.85)

        # First chunk establishes Voice A
        spk1, vid1, conf1 = registry.resolve_chunk_speaker(wav_voice_1a)
        self.assertEqual(vid1, "Voice A")
        self.assertEqual(spk1, "Voice A")

        # Higher-pitch chunk establishes Voice B
        spk2, vid2, conf2 = registry.resolve_chunk_speaker(wav_voice_2a)
        self.assertEqual(vid2, "Voice B")
        self.assertEqual(spk2, "Voice B")

        # Second chunk of tone 1 should match Voice A with high confidence
        spk1_again, vid1_again, conf1_again = registry.resolve_chunk_speaker(wav_voice_1b)
        self.assertEqual(vid1_again, "Voice A")
        self.assertGreater(conf1_again, 0.90)

        # Second chunk of tone 2 should match Voice B with high confidence
        spk2_again, vid2_again, conf2_again = registry.resolve_chunk_speaker(wav_voice_2b)
        self.assertEqual(vid2_again, "Voice B")
        self.assertGreater(conf2_again, 0.90)

    def test_operator_speaker_hint_full_lifecycle_and_retroactive_update(self) -> None:
        """Verify operator hint retroactively renames 100% of segments and carries forward to new chunks."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Technical Alignment Sync",
            meeting_date="10/04/2026",
            attendees=["Samantha Vance", "Alex Mercer", "Marcus Vance"],
            output_dir=self.tmp_path / "output",
            mock_mode=True,
            enable_copilot=False,
            fast_start=True,
        )
        self._recorders.append(rec)
        rec.speaker_hint_manager.ledger_path = self.ledger_path

        # Simulate transcribing chunks across two voices
        wav_v1_0 = _generate_tone_wav(rec.chunks_dir / "chunk_0000.wav", freq=150.0)
        wav_v2_1 = _generate_tone_wav(rec.chunks_dir / "chunk_0001.wav", freq=350.0)
        wav_v1_2 = _generate_tone_wav(rec.chunks_dir / "chunk_0002.wav", freq=150.0)

        with patch.object(
            rec,
            "_transcribe_chunk",
            side_effect=[
                "Welcome to the technical alignment call everyone.",
                "Glad to be here, let us review the timeline.",
                "Our team will handle the core deployment deliverable.",
            ],
        ):
            rec.process_chunk(wav_v1_0, 0)
            rec.process_chunk(wav_v2_1, 1)
            rec.process_chunk(wav_v1_2, 2)

        # Verify initial segments file state
        seg_lines = rec.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(seg_lines), 3)
        s0 = json.loads(seg_lines[0])
        s1 = json.loads(seg_lines[1])
        s2 = json.loads(seg_lines[2])
        self.assertEqual(s0["voice_id"], "Voice A")
        self.assertEqual(s1["voice_id"], "Voice B")
        self.assertEqual(s2["voice_id"], "Voice A")

        # Apply operator hint: "technical alignment call" was spoken by Samantha Vance
        hint_res = rec.apply_speaker_hint(
            quote="technical alignment call",
            speaker_name="Samantha Vance",
            operator_id="operator_test",
        )
        self.assertEqual(hint_res.status, "PASS")
        self.assertEqual(hint_res.voice_id, "Voice A")
        self.assertEqual(hint_res.speaker_name, "Samantha Vance")
        self.assertEqual(hint_res.updated_segments_count, 2)

        # Verify retroactive updates in segments.jsonl
        updated_lines = rec.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        u0 = json.loads(updated_lines[0])
        u1 = json.loads(updated_lines[1])
        u2 = json.loads(updated_lines[2])

        self.assertEqual(u0["speaker"], "Samantha Vance")
        self.assertTrue(u0.get("hint_applied"))
        self.assertEqual(u1["speaker"], "Voice B")
        self.assertFalse(u1.get("hint_applied", False))
        self.assertEqual(u2["speaker"], "Samantha Vance")
        self.assertTrue(u2.get("hint_applied"))

        # Verify retroactive updates in transcript.md
        md_text = rec.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Samantha Vance: Welcome to the technical alignment call everyone.", md_text)
        self.assertIn("Samantha Vance: Our team will handle the core deployment deliverable.", md_text)
        self.assertNotIn("Voice A:", md_text)
        self.assertIn("Voice B:", md_text)

        # Forward carry: Next chunk of Voice A resolves to Samantha Vance immediately
        wav_v1_3 = _generate_tone_wav(rec.chunks_dir / "chunk_0003.wav", freq=150.0)
        with patch.object(
            rec,
            "_transcribe_chunk",
            return_value="Wrapping up our review of the deployment items.",
        ):
            rec.process_chunk(wav_v1_3, 3)

        updated_lines_4 = rec.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(updated_lines_4), 4)
        u3 = json.loads(updated_lines_4[3])
        self.assertEqual(u3["voice_id"], "Voice A")
        self.assertEqual(u3["speaker"], "Samantha Vance")

        # Stop recorder and verify sidecars
        rec.stop()
        base_dir = rec.output_dir
        map_json = base_dir / f"{rec.clean_slug}_speaker_map.json"
        hints_json = base_dir / "speaker_hints.json"
        self.assertTrue(map_json.is_file(), f"Expected {map_json} to exist")
        self.assertTrue(hints_json.is_file(), f"Expected {hints_json} to exist")

        # Stage U0 loader integration verification
        colocated_map = load_colocated_speaker_map(rec.output_dir)
        self.assertIsNotNone(colocated_map)
        self.assertEqual(colocated_map.get("Voice A"), "Samantha Vance")

    def test_ed25519_cryptographic_signature_in_hitl_ledger(self) -> None:
        """Verify that every applied hint produces an authentic Ed25519-signed ledger record."""
        manager = SpeakerHintManager(
            output_dir=self.tmp_path / "output",
            cache_dir=self.tmp_path / "cache",
            attendees=["Samantha Vance", "Alex Mercer"],
            ledger_path=self.ledger_path,
        )
        target_segments = self.tmp_path / "cache" / "segments.jsonl"
        target_segments.parent.mkdir(parents=True, exist_ok=True)
        seg = {
            "chunk_idx": 0,
            "timestamp": "00:00",
            "text": "Initiating live migration protocol",
            "voice_id": "Voice A",
            "speaker": "Voice A",
        }
        target_segments.write_text(json.dumps(seg) + "\n", encoding="utf-8")

        res = manager.apply_hint(
            quote="migration protocol",
            speaker_name="Samantha Vance",
            segments_jsonl_path=target_segments,
            operator_id="operator_agent",
            meeting_slug="migration_test",
        )
        self.assertEqual(res.status, "PASS")
        self.assertTrue(self.ledger_path.is_file())

        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        decisions = data.get("decisions", [])
        self.assertGreaterEqual(len(decisions), 1)
        ledger_entry = decisions[-1]

        # Cryptographic verification
        _, pub_key = get_governed_hitl_keypair()
        payload = ledger_entry["payload"]
        sig = ledger_entry["signature"]

        is_valid = verify_hitl_approval_signature(
            payload=payload,
            signature_b64=sig,
            public_key=pub_key,
        )
        self.assertTrue(is_valid, "Ed25519 signature verification failed on ledger entry")
        self.assertEqual(ledger_entry["operator_id"], "operator_agent")
        self.assertEqual(payload["operator"]["subject"], "operator_agent")
        self.assertEqual(payload["metadata"]["speaker"], "Samantha Vance")
        self.assertTrue(payload["metadata"]["operator_verified"])

    def test_inline_chat_attribution_triggers_speaker_hint(self) -> None:
        """Verify that inline operator chat message with quote attribution triggers speaker hint."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Chat Attribution Test",
            meeting_date="10/04/2026",
            attendees=["Samantha Vance", "Alex Mercer"],
            output_dir=self.tmp_path / "output",
            mock_mode=True,
            enable_copilot=False,
            fast_start=True,
        )
        self._recorders.append(rec)
        rec.speaker_hint_manager.ledger_path = self.ledger_path

        wav = _generate_tone_wav(rec.chunks_dir / "chunk_0000.wav", freq=300.0)
        with patch.object(
            rec,
            "_transcribe_chunk",
            return_value="We will finalize the database schema by noon.",
        ):
            rec.process_chunk(wav, 0)

        # Operator sends inline chat message
        rec.record_chat_message("Samantha: 'finalize the database schema'")

        # Verify segment was retroactively updated
        lines = rec.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        seg = json.loads(lines[0])
        self.assertEqual(seg["speaker"], "Samantha Vance")
        self.assertTrue(seg.get("hint_applied"))

    def test_cli_nee_speaker_hint_command(self) -> None:
        """Verify nee speaker-hint CLI command updates meeting directory cleanly."""
        import argparse

        meeting_dir = self.tmp_path / "meeting_dir"
        meeting_dir.mkdir(parents=True, exist_ok=True)
        seg_file = meeting_dir / "01_10-04-2026_test_meeting_segments.jsonl"
        md_file = meeting_dir / "01_10-04-2026_test_meeting_live_transcript.md"

        seg = {
            "chunk_idx": 0,
            "timestamp": "00:05",
            "text": "Let us proceed with the system test.",
            "voice_id": "Voice A",
            "speaker": "Voice A",
        }
        seg_file.write_text(json.dumps(seg) + "\n", encoding="utf-8")
        md_file.write_text("[00:05] Voice A: Let us proceed with the system test.\n", encoding="utf-8")

        args = argparse.Namespace(
            quote="proceed with the system test",
            speaker="Alex Mercer",
            session=None,
            active=False,
            meeting_dir=str(meeting_dir),
            force=True,
            runtime_root=str(self.tmp_path),
        )

        result = handle_speaker_hint(args)
        self.assertEqual(result.get("status"), "PASS")

        updated_seg = json.loads(seg_file.read_text(encoding="utf-8").strip())
        self.assertEqual(updated_seg["speaker"], "Alex Mercer")
        self.assertTrue(updated_seg.get("hint_applied"))

        updated_md = md_file.read_text(encoding="utf-8")
        self.assertIn("Alex Mercer: Let us proceed with the system test.", updated_md)


if __name__ == "__main__":
    unittest.main()
