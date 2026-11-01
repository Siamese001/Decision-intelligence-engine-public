"""
Wave 3 End-to-End Verification:
Stereo Channel ASR (Ch0 Remote / Ch1 Host), LIVE_PREVIEW Cache Tier Gating,
force_rerun=True Authority, Container Source Priority, and Socket Airgap.
"""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from dual_pipeline.audio_ingestion_core.acoustic_airgap import (
    AcousticAirgapContext,
    AirgapViolationError,
    write_acoustic_receipt,
)
from dual_pipeline.audio_ingestion_core.acoustic_cache import (
    find_matching_sealed_cache,
    try_load_transcript_cache,
)
from dual_pipeline.audio_ingestion_core.stereo_alignment import (
    align_segments_via_stereo_energy,
    redecode_overlapping_stereo_windows,
    transcribe_stereo_channels_independently,
)
from dual_pipeline.canonical_pipeline.content_loader import _resolve_container_source_file
from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal, verify_cache_seal


def _write_overlapping_stereo_wav(path: Path, sr: int = 16000, duration_s: float = 3.0) -> None:
    n = int(sr * duration_s)
    t = np.arange(n, dtype=np.float32) / float(sr)
    ch0 = (np.sin(2.0 * np.pi * 440.0 * t) * 7000.0).astype(np.int16)
    ch1 = (np.sin(2.0 * np.pi * 880.0 * t) * 7500.0).astype(np.int16)
    interleaved = np.empty(n * 2, dtype=np.int16)
    interleaved[0::2] = ch0
    interleaved[1::2] = ch1
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(interleaved.tobytes())


class TestStereoMLXAuthorityAndAirgapE2E(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wave3_e2e_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stereo_ch0_ch1_asr_and_overlap_redecode_polarity(self) -> None:
        """Verifies Ch0 (Remote -> SPEAKER_00) and Ch1 (Host -> SPEAKER_01) preserve separate turns."""
        stereo_wav = self.root / "00_2026-10-29_arch_master_120000.wav"
        _write_overlapping_stereo_wav(stereo_wav)

        def _fake_channel_asr(ch_wav_path: str, ch_idx: int):
            if ch_idx == 0:
                return [
                    {
                        "start": 0.0,
                        "end": 1.8,
                        "text": "Remote architecture proposal on channel zero.",
                        "words": [{"word": "Remote", "start": 0.0, "end": 0.5}],
                    }
                ]
            return [
                {
                    "start": 0.2,
                    "end": 2.0,
                    "text": "Host technical counter-proposal on channel one.",
                    "words": [{"word": "Host", "start": 0.2, "end": 0.6}],
                }
            ]

        merged = transcribe_stereo_channels_independently(
            stereo_wav, _fake_channel_asr, boundary_overlap_sec=1.5
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["channel"], 0)
        self.assertEqual(merged[0]["speaker"], "SPEAKER_00")
        self.assertIn("Remote architecture", merged[0]["text"])
        self.assertEqual(merged[1]["channel"], 1)
        self.assertEqual(merged[1]["speaker"], "SPEAKER_01")
        self.assertIn("Host technical", merged[1]["text"])

        # Also verify redecode_overlapping_stereo_windows maintains identical polarity (Ch0=SPEAKER_00, Ch1=SPEAKER_01)
        overlap_segs = [{"start": 0.0, "end": 2.0, "text": "mixed", "overlap": True, "overlap_redecode_required": True}]
        redecoded = redecode_overlapping_stereo_windows(
            overlap_segs,
            stereo_wav,
            transcribe_window_fn=lambda _p, ch, _s, _e: "Remote speech." if ch == 0 else "Host speech.",
        )
        self.assertEqual(len(redecoded), 2)
        self.assertEqual(redecoded[0]["channel"], 0)
        self.assertEqual(redecoded[0]["speaker"], "SPEAKER_00")
        self.assertEqual(redecoded[0]["text"], "Remote speech.")
        self.assertEqual(redecoded[1]["channel"], 1)
        self.assertEqual(redecoded[1]["speaker"], "SPEAKER_01")
        self.assertEqual(redecoded[1]["text"], "Host speech.")

    def test_live_preview_cache_seal_never_skips_authoritative_u0_and_container_prioritizes_master_wav(self) -> None:
        """Verifies LIVE_PREVIEW seals are rejected for authoritative U0 fast-path and container selects master WAV."""
        container = self.root / "meeting_container"
        cache_dir = container / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        master_wav = cache_dir / "00_2026-10-29_test_master_120000.wav"
        _write_overlapping_stereo_wav(master_wav, duration_s=1.0)
        live_md = container / "01_2026-10-29_test_live_transcript.md"
        live_md.write_text("# Live Meeting Transcript\n[00:00] Speaker: Rough preview.\n", encoding="utf-8")

        # Container resolution MUST prioritize 00_*_master*.wav over 01_*_live_transcript.md
        resolved_src = _resolve_container_source_file(container)
        self.assertEqual(resolved_src, master_wav)

        # Create a LIVE_PREVIEW sealed cache
        seg_file = cache_dir / "00_test_abc123.segments.json"
        tx_file = cache_dir / "00_test_abc123.transcript_cache.json"
        utt_file = cache_dir / "00_test_abc123.utterances.json"
        seg_file.write_text(json.dumps([{"speaker": "Speaker", "text": "Rough preview turn list covering over fifty characters easily."}]), encoding="utf-8")
        tx_file.write_text("[00:00:00] Speaker: Rough preview turn list covering over fifty characters easily.", encoding="utf-8")
        utt_file.write_text(json.dumps([{"speaker": "Speaker", "text": "Rough preview turn list covering over fifty characters easily."}]), encoding="utf-8")

        seal_path = create_cache_seal(
            structured_file=seg_file,
            cache_file=tx_file,
            utterance_file=utt_file,
            audio_sha256="deadbeef12345678",
            cache_key="abc123",
            cache_tier="LIVE_PREVIEW",
        )
        seal_data = verify_cache_seal(seg_file, tx_file, utt_file, seal_path)
        self.assertEqual(seal_data["cache_tier"], "LIVE_PREVIEW")

        # Authoritative cache loaders must reject LIVE_PREVIEW tier by default
        meta: dict = {}
        self.assertIsNone(try_load_transcript_cache(tx_file, seg_file, utt_file, meta))
        self.assertIsNone(find_matching_sealed_cache(cache_dir, "deadbeef12345678", "deadbeef123456780000", meta))

        # Now seal as AUTHORITATIVE_MASTER and verify it loads cleanly, unless force_rerun=True
        create_cache_seal(
            structured_file=seg_file,
            cache_file=tx_file,
            utterance_file=utt_file,
            audio_sha256="deadbeef12345678",
            cache_key="abc123",
            cache_tier="AUTHORITATIVE_MASTER",
        )
        loaded = try_load_transcript_cache(tx_file, seg_file, utt_file, meta)
        self.assertIsNotNone(loaded)
        self.assertIsNone(try_load_transcript_cache(tx_file, seg_file, utt_file, {"force_rerun": True}))

    def test_acoustic_airgap_context_and_honest_fallback_telemetry(self) -> None:
        """Verifies AcousticAirgapContext blocks outbound sockets and write_acoustic_receipt reports honest alignment backend."""
        with AcousticAirgapContext():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with self.assertRaises(AirgapViolationError):
                    s.connect(("8.8.8.8", 443))
            finally:
                s.close()

        rcpt_path = write_acoustic_receipt(
            safe_stem="wave3_airgap_test",
            model_name="mlx-community/whisper-large-v3-turbo",
            diarization_engine=None,
            output_dir=self.root,
            alignment_backend="mlx_word_timestamps_fallback",
        )
        self.assertIsNotNone(rcpt_path)
        rcpt = json.loads(rcpt_path.read_text(encoding="utf-8"))
        self.assertEqual(rcpt["alignment_backend"], "mlx_word_timestamps_fallback")


if __name__ == "__main__":
    unittest.main()
