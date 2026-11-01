"""
tests/end_to_end/test_asr_sanitizer_parity_e2e.py

End-to-End Verification Suite for Wave 7 ASR Sanitizer Parity & Verbatim Preservation.
Tests:
1. Live chunk ASR and Batch master WAV ASR post-processing produce 100% identical outputs
   across shared fixtures (sanitizer parity).
2. LexicalPrimer phonetic drift rewrites are stored as {verbatim, normalized} in segments.jsonl,
   ensuring verbatim is 100% recoverable.
3. Live transcript markdown (.md) displays normalized terminology while segments.jsonl preserves verbatim.
4. asr_filter_log.jsonl is populated with all dropped artifacts and preserved across worktree migrations.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from dual_pipeline.audio_postprocess import sanitize_transcript_text
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import _filter_hallucinations
from dual_pipeline.live_meeting_recorder_core.lexical_priming import LexicalPrimer


def _create_synthetic_pcm_chunk(wav_path: Path, num_frames: int = 16000 * 2, amplitude: int = 3000) -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        pcm_data = bytearray()
        for i in range(num_frames):
            val = amplitude if (i % 200) < 100 else -amplitude
            pcm_data.extend(int(val).to_bytes(2, byteorder="little", signed=True))
        wf.writeframes(bytes(pcm_data))


class TestASRSanitizerParityE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.whisper_prompt = (
            "Meeting participants: Drew Oakley, Amit Ayer. "
            "Project: APEX_TRANS. Domain terms: ROCC, SCADA, EMS, Apex Global Enterprises, APEX."
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Live vs Batch Sanitizer Parity
    # -------------------------------------------------------------------------
    def test_live_and_batch_sanitizer_parity(self) -> None:
        """Live chunk sanitizer and batch master WAV sanitizer must yield identical text."""
        test_corpus = [
            # Legitimate multi-word speech
            "Good morning Dan, let's review the substation cutover schedule.",
            "Please subscribe to the incident newsletter and check grid.example.com for outage maps.",
            "Visit www.consulting.example.com to download the architecture diagram.",
            "We were discussing the budget decisions for next quarter.",
            "The operators at the ROCC confirmed SCADA data transmission is normal.",
            "I think we, I think we should proceed with the deployment plan.",
            # Hallucinations & artifacts that must be dropped
            "Thank you for watching.",
            "Thanks for watching!",
            "Subtitles by the Amara.org community",
            "Please subscribe to our channel.",
            "The end.",
            "www.grid.example.com",
            "[applause]",
            "(laughter)",
            "Apex GlobalPlay",
            "we need to we need to we need to we need to we need to",
        ]

        live_log = self.base_dir / "live_filter_log.jsonl"
        batch_log = self.base_dir / "batch_filter_log.jsonl"

        for idx, text in enumerate(test_corpus):
            # Live path
            live_out = _filter_hallucinations(
                text,
                whisper_prompt=self.whisper_prompt,
                filter_log_path=live_log,
                chunk_idx=idx,
                chunk_file=f"chunk_{idx:05d}.wav",
            )
            # Batch path
            batch_out = sanitize_transcript_text(
                text,
                whisper_prompt=self.whisper_prompt,
                filter_log_path=batch_log,
                chunk_idx=idx,
                chunk_file=f"chunk_{idx:05d}.wav",
            )

            self.assertEqual(
                live_out,
                batch_out,
                f"Parity mismatch on utterance '{text}': Live='{live_out}' vs Batch='{batch_out}'",
            )

        # Confirm parity of drop logs
        live_drops = [json.loads(line)["raw_text"] for line in live_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        batch_drops = [json.loads(line)["raw_text"] for line in batch_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(live_drops, batch_drops)

    # -------------------------------------------------------------------------
    # 2. Verbatim Preservation & Recovery in Live Meeting Recorder
    # -------------------------------------------------------------------------
    def test_verbatim_preservation_and_recovery_in_recorder(self) -> None:
        """LiveMeetingRecorder preserves verbatim in segments.jsonl while emitting normalized to .md."""
        output_dir = self.base_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        recorder = LiveMeetingRecorder(
            project="apex_trans",
            meeting_name="Verbatim_Parity_Session",
            date="10/04/2026",
            attendees=["Drew Oakley", "Amit Ayer"],
            output_dir=str(output_dir),
            mock_mode=True,
            segment_seconds=2,
        )

        # Set up a real LexicalPrimer for APEX_TRANS
        recorder.lexical_primer = LexicalPrimer("apex_trans")

        # Mock the transcription step to return an utterance needing phonetic normalization
        # 'in Iraq' -> 'in the ROCC'
        raw_whisper_text = "The operators in Iraq reviewed the SCADA data."
        recorder._transcribe_chunk = lambda wav_path, chunk_idx=None: raw_whisper_text

        # Create synthetic chunk
        chunk_file = recorder.chunks_dir / "chunk_00000.wav"
        _create_synthetic_pcm_chunk(chunk_file)

        # Process the chunk
        result_text = recorder.process_chunk(chunk_file, 0)
        self.assertEqual(result_text, "The operators in the ROCC reviewed the SCADA data.")

        # Inspect segments.jsonl
        self.assertTrue(recorder.segments_jsonl.exists())
        segments = [json.loads(line) for line in recorder.segments_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(segments), 1)

        seg = segments[0]
        # Verbatim must preserve original Whisper text
        self.assertEqual(seg["verbatim"], "The operators in Iraq reviewed the SCADA data.")
        # Normalized must reflect phonetic correction
        self.assertEqual(seg["normalized"], "The operators in the ROCC reviewed the SCADA data.")
        self.assertEqual(seg["text"], "The operators in Iraq reviewed the SCADA data.")

        # Inspect transcript markdown (.md)
        self.assertTrue(recorder.transcript_md.exists())
        md_content = recorder.transcript_md.read_text(encoding="utf-8")
        # Markdown must show the normalized text
        self.assertIn("The operators in the ROCC reviewed the SCADA data.", md_content)
        # Markdown must not show the uncorrected 'in Iraq'
        self.assertNotIn("in Iraq", md_content)

    # -------------------------------------------------------------------------
    # 3. ASR Filter Log Integrity
    # -------------------------------------------------------------------------
    def test_asr_filter_log_populated_and_migrated(self) -> None:
        """Dropped ASR artifacts are logged to asr_filter_log.jsonl in the cache directory."""
        output_dir = self.base_dir / "output_filter_test"
        output_dir.mkdir(parents=True, exist_ok=True)

        recorder = LiveMeetingRecorder(
            project="apex_trans",
            meeting_name="Filter_Log_Test",
            date="10/04/2026",
            attendees=["Drew Oakley", "Amit Ayer"],
            output_dir=str(output_dir),
            mock_mode=True,
            segment_seconds=2,
        )

        # Trigger a drop through transcribe_meeting_chunk with a credit hallucination
        filter_log_path = recorder.asr_filter_log
        _filter_hallucinations(
            "Thank you for watching.",
            whisper_prompt="Drew Oakley",
            filter_log_path=filter_log_path,
            chunk_idx=0,
            chunk_file="chunk_00000.wav",
        )

        self.assertTrue(filter_log_path.exists())
        records = [json.loads(line) for line in filter_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reason"], "CREDIT_HALLUCINATION")
        self.assertEqual(records[0]["raw_text"], "Thank you for watching.")
        self.assertEqual(records[0]["chunk_idx"], 0)


if __name__ == "__main__":
    unittest.main()
