"""
tests/end_to_end/test_silent_durable_attribution_e2e.py

Wave 4 End-to-End Test Suite: Silent Durable Attribution Lifecycle.
Verifies the full end-to-end contract:
  1. Early speaker hints arriving before audio segments exist are stored as QUEUED.
  2. Unknown timestamps are preserved as explicit None without synthetic 0.0 substitution.
  3. Processing audio chunks retroactively reconciles QUEUED hints to matching voice clusters.
  4. Cryptographic Ed25519 signature is registered in hitl_decision_ledger.json.
  5. Segment JSONL and transcript markdown are updated retroactively.
  6. Unmatched hints remain truthfully QUEUED/PENDING with zero fabricated commitments.
  7. Exact deduplication prevents duplicate queue records and duplicate ledger entries.
"""
from __future__ import annotations

import argparse
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

import pytest

from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.hitl_governance_core.crypto import (
    get_governed_hitl_keypair,
    verify_hitl_approval_signature,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import (
    DurableSpeakerHintQueue,
)


def _generate_test_wav(
    path: Path,
    freq: float = 220.0,
    duration_sec: float = 2.0,
    sample_rate: int = 16000,
    amplitude: float = 12000.0,
) -> Path:
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


@pytest.mark.e2e
class TestSilentDurableAttributionE2E(unittest.TestCase):
    """End-to-End lifecycle tests for silent durable speaker attribution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_w4_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self.output_dir = self.tmp_path / "output"
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"
        self._recorders: list[Any] = []

    def tearDown(self) -> None:
        for rec in self._recorders:
            with contextlib.suppress(Exception):
                rec.stop()
            with contextlib.suppress(Exception):
                rec._release_slug_lock()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_early_speaker_hint_reconciles_asynchronously_e2e(self) -> None:
        """
        Verify that an early hint arriving before segments exist stays QUEUED,
        and once an audio chunk is transcribed with the matching quote,
        asynchronously reconciles to the voice cluster and updates the transcript.
        """
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Project Beacon Wave 4 Test",
            meeting_date="10/06/2026",
            attendees=["Speaker_Alpha", "Speaker_Beta", "Alex Rivera"],
            output_dir=self.output_dir,
            mock_mode=True,
            enable_copilot=False,
            fast_start=True,
        )
        self._recorders.append(rec)
        rec.speaker_hint_manager.ledger_path = self.ledger_path

        # 1. Early hint before any audio segments exist
        raw_hint = 'Speaker_Alpha - "Dave real quick, do you recall offhand"'
        attributions = rec.record_chat_message(raw_hint)

        assert len(attributions) == 1
        assert attributions[0].speaker_name == "Speaker_Alpha"
        assert attributions[0].session_elapsed_seconds is None  # Explicit None

        # Verify hint queue holds the hint as QUEUED
        pending = rec.speaker_hint_queue.get_pending_hints()
        assert len(pending) == 1
        assert pending[0].speaker_name == "Speaker_Alpha"
        assert pending[0].quote == "Dave real quick, do you recall offhand"
        assert pending[0].status == "QUEUED"
        assert pending[0].session_elapsed_seconds is None

        # Segments file does not exist yet
        assert not rec.segments_jsonl.exists()

        # 2. Audio chunk arrives and is transcribed
        wav = _generate_test_wav(rec.chunks_dir / "chunk_0000.wav", freq=260.0)
        with patch.object(
            rec,
            "_transcribe_chunk",
            return_value="Dave real quick, do you recall offhand what the architecture looks like?",
        ):
            rec.process_chunk(wav, 0)

        # 3. Verify automatic asynchronous reconciliation
        assert rec.segments_jsonl.exists()
        reconciled = rec.speaker_hint_queue.get_reconciled_hints()
        assert len(reconciled) == 1
        assert reconciled[0].speaker_name == "Speaker_Alpha"
        assert reconciled[0].status == "RECONCILED"
        assert reconciled[0].voice_id is not None

        # 4. Verify segment retroactively updated
        lines = rec.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        seg = json.loads(lines[0])
        assert seg["speaker"] == "Speaker_Alpha"
        assert seg["hint_applied"] is True

        # 5. Verify transcript markdown updated
        assert rec.transcript_md.exists()
        md_text = rec.transcript_md.read_text(encoding="utf-8")
        assert "Speaker_Alpha:" in md_text

        # 6. Verify authentic Ed25519 signature in ledger
        assert self.ledger_path.is_file()
        ledger_data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        decisions = ledger_data.get("decisions", [])
        assert len(decisions) == 1

        _, pub_key = get_governed_hitl_keypair()
        is_valid = verify_hitl_approval_signature(
            payload=decisions[0]["payload"],
            signature_b64=decisions[0]["signature"],
            public_key=pub_key,
        )
        assert is_valid is True

    def test_cli_speaker_hint_early_queuing_e2e(self) -> None:
        """
        Verify `nee speaker-hint` called on a meeting directory before segments exist
        enqueues into DurableSpeakerHintQueue with status QUEUED without failing.
        """
        meeting_dir = self.tmp_path / "meeting_session"
        meeting_dir.mkdir(parents=True, exist_ok=True)

        args = argparse.Namespace(
            quote="I have a registration workflow",
            speaker="Speaker_Beta",
            force=False,
            meeting_dir=str(meeting_dir),
            runtime_root=self.tmp_path,
        )

        res = handle_speaker_hint(args)
        assert res["status"] == "PASS"
        assert res["action"] == "QUEUED"
        assert res["speaker_name"] == "Speaker_Beta"
        assert res["is_new"] is True

        # Check queue file
        queue_path = meeting_dir / "speaker_hints_queue.json"
        assert queue_path.is_file()
        queue = DurableSpeakerHintQueue(queue_path=queue_path)
        pending = queue.get_pending_hints()
        assert len(pending) == 1
        assert pending[0].speaker_name == "Speaker_Beta"
        assert pending[0].quote == "I have a registration workflow"
        assert pending[0].status == "QUEUED"

    def test_unmatched_hint_truthfully_pending_e2e(self) -> None:
        """
        Verify that a hint for words never spoken remains truthfully QUEUED
        without fabricating commitments or hallucinating text.
        """
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Pending Test",
            meeting_date="10/06/2026",
            attendees=["Alex Rivera"],
            output_dir=self.output_dir,
            mock_mode=True,
            enable_copilot=False,
            fast_start=True,
        )
        self._recorders.append(rec)

        # Submit hint for unsaid speech
        rec.record_chat_message('Alex Rivera - "A completely unsaid quote"')

        # Process unrelated chunk
        wav = _generate_test_wav(rec.chunks_dir / "chunk_0000.wav", freq=320.0)
        with patch.object(
            rec,
            "_transcribe_chunk",
            return_value="We are discussing server deployment pipelines.",
        ):
            rec.process_chunk(wav, 0)

        # Hint must remain QUEUED
        assert len(rec.speaker_hint_queue.get_pending_hints()) == 1
        assert len(rec.speaker_hint_queue.get_reconciled_hints()) == 0

        # Segment must retain default speaker
        lines = rec.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        seg = json.loads(lines[0])
        assert seg["speaker"] != "Alex Rivera"
        assert "hint_applied" not in seg

    def test_deduplication_and_zero_chat_noise_e2e(self) -> None:
        """
        Verify repeated identical hints are deduplicated into exactly 1 queue record
        and 1 ledger entry, producing zero chat noise.
        """
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Dedupe Test",
            meeting_date="10/06/2026",
            attendees=["Speaker_Alpha"],
            output_dir=self.output_dir,
            mock_mode=True,
            enable_copilot=False,
            fast_start=True,
        )
        self._recorders.append(rec)
        rec.speaker_hint_manager.ledger_path = self.ledger_path

        # Send same hint 3 times
        hint_text = 'Speaker_Alpha - "Dave real quick"'
        r1 = rec.record_chat_message(hint_text)
        r2 = rec.record_chat_message(hint_text)
        r3 = rec.record_chat_message(hint_text)

        assert len(r1) == 1
        assert len(r2) == 0  # Deduplicated
        assert len(r3) == 0  # Deduplicated

        # Exactly 1 in queue
        assert len(rec.speaker_hint_queue._hints) == 1

        # Process audio to reconcile
        wav = _generate_test_wav(rec.chunks_dir / "chunk_0000.wav", freq=260.0)
        with patch.object(rec, "_transcribe_chunk", return_value="Dave real quick please"):
            rec.process_chunk(wav, 0)

        assert len(rec.speaker_hint_queue.get_reconciled_hints()) == 1

        # Only 1 ledger record
        ledger_data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        assert len(ledger_data.get("decisions", [])) == 1
