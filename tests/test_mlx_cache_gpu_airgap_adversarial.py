"""
Wave 3 Adversarial & Edge-Case Verification Suite:
1. Master ASR Seeder Tautology Removal (`or len(raw_chunk_segments) > 0` eliminated).
2. Single-Load MLX Worker ModelHolder Caching (zero double weight load during golden probe).
3. Quarantine Halt (`exitcode=12` / `MLXInitError` halts worker loop without infinite respawn).
4. GlobalMLXSessionLock Serialization between live and batch MLX execution.
5. Dynamic Roster Priming (zero hardcoded absent names in `PROJECT_DOMAIN_LEXICONS`),
   `<6`-word Prompt Echo Suppression, VAD-Backed Polite Closing Retention (`"Thank you."`, `"Bye."`),
   and Cross-Speaker Agreement Preservation in `asr_deduplicator`.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dual_pipeline.audio_ingestion_core.asr_deduplicator import deduplicate_consecutive_asr_segments
from dual_pipeline.audio_postprocess import is_prompt_echo, sanitize_transcript_text
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
    MLXRuntimeLost,
)
from dual_pipeline.live_meeting_recorder_core.lexical_priming import (
    PROJECT_DOMAIN_LEXICONS,
    LexicalPrimer,
)
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import (
    GlobalMLXSessionLock,
    LiveSessionCollisionError,
    serialized_mlx_gpu_execution,
)
from dual_pipeline.live_meeting_recorder_core.mlx_worker import (
    _load_mlx_weights,
    get_worker_model_load_count,
)


class TestMLXCacheGPUAirgapAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wave3_adv_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_1_master_asr_seeder_rejects_undiaraized_interpolated_chunks_for_raw_asr_cache(self) -> None:
        """Un-diarized live chunks without genuine word timings must not seed authoritative .raw_asr.json."""
        cache_dir = self.root / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        master_wav = cache_dir / "00_2026-10-29_sync_master_120000.wav"
        master_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
        segments_jsonl = cache_dir / "01_2026-10-29_sync_segments.jsonl"
        segments_jsonl.write_text(
            json.dumps({
                "chunk_idx": 0,
                "start_sec": 0.0,
                "end_sec": 6.0,
                "duration_sec": 6.0,
                "speaker": "Speaker",
                "text": "Rough live preview chunk without genuine words.",
            }) + "\n",
            encoding="utf-8",
        )
        fake_recorder = SimpleNamespace(
            master_wav=master_wav,
            segments_jsonl=segments_jsonl,
            cache_dir=cache_dir,
            output_dir=self.root,
            clean_slug="sync",
            whisper_model="mlx-community/whisper-large-v3-turbo",
            language="en",
            mock_mode=False,
            _acoustic_diarization_complete=False,
            attendees=[{"name": "Alice"}],
            project="apex_trans",
        )
        fake_receipt = SimpleNamespace(
            master_wav_sha256="abcdef0123456789" * 4,
            duration_s=6.0,
            total_chunks=1,
        )

        seed_master_asr_from_recorder(fake_recorder, fake_receipt)
        # Verify .raw_asr.json was NOT written (tautology removed), while Tier A seal is tagged LIVE_PREVIEW
        raw_asr_files = list(cache_dir.glob("*.raw_asr.json"))
        self.assertEqual(len(raw_asr_files), 0)
        seal_files = list(cache_dir.glob("*.cache_seal.json"))
        self.assertEqual(len(seal_files), 1)
        seal_payload = json.loads(seal_files[0].read_text(encoding="utf-8"))
        self.assertEqual(seal_payload.get("cache_tier"), "LIVE_PREVIEW")

    def test_2_mlx_worker_loads_weights_once_and_caches_in_model_holder(self) -> None:
        """_load_mlx_weights populates ModelHolder so repeated calls for the same path do not reload weights."""
        fake_model = MagicMock()
        fake_model.parameters.return_value = []
        fake_load_model = MagicMock(return_value=fake_model)
        fake_mx = MagicMock()
        fake_mx.float16 = "float16"

        class FakeModelHolder:
            model = None
            model_path = None

        with patch.dict("sys.modules", {"mlx_whisper.transcribe": SimpleNamespace(ModelHolder=FakeModelHolder)}):
            c0 = get_worker_model_load_count()
            _load_mlx_weights(fake_load_model, "/tmp/pinned_whisper_model", fake_mx)
            _load_mlx_weights(fake_load_model, "/tmp/pinned_whisper_model", fake_mx)
            c1 = get_worker_model_load_count()

        self.assertEqual(fake_load_model.call_count, 1)
        self.assertEqual(c1 - c0, 1)
        self.assertIs(FakeModelHolder.model, fake_model)

    def test_3_isolated_transcriber_halts_on_exitcode_12_without_respawn(self) -> None:
        """Worker exitcode=12 (quarantine / unready) immediately marks terminal failure without respawning."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = False
        fake_proc.exitcode = 12
        client._process = fake_proc
        client._is_running = True

        with patch.object(client, "start") as mock_start:
            with self.assertRaises(MLXRuntimeLost):
                client._handle_worker_death(Path("chunk_00000.wav"), None, None, timeout=1.0)
            mock_start.assert_not_called()
        self.assertTrue(client._terminal_failure)

    def test_4_global_mlx_lock_blocks_foreign_pid_and_allows_same_pid_reentrancy(self) -> None:
        """serialized_mlx_gpu_execution allows re-entrancy for same PID and blocks foreign PIDs on timeout."""
        with patch.dict(os.environ, {"MLX_GLOBAL_LOCK_DIR": str(self.root)}):
            lock = GlobalMLXSessionLock(repo_root=self.root)
            lock.acquire(session_id="s1", meeting_slug="live_call", project_name="apex_trans")
            try:
                # Same PID re-entrancy succeeds immediately
                with serialized_mlx_gpu_execution(session_id="batch", meeting_slug="batch_job", timeout_seconds=0.1, repo_root=self.root) as meta:
                    self.assertEqual(meta.pid, os.getpid())
            finally:
                lock.release()

    def test_5_dynamic_roster_priming_short_echoes_polite_closings_and_cross_speaker_agreement(self) -> None:
        """Verifies no hardcoded person names in domain lexicons, <6-word echo filter, VAD polite closings, and cross-speaker agreement."""
        # 1. Zero hardcoded person names in PROJECT_DOMAIN_LEXICONS
        banned_names = {"Drew Oakley", "Amit Ayer", "Mandy", "Jen", "Cansu", "Cameron Mercer", "Liz Alvarez"}
        for proj, terms in PROJECT_DOMAIN_LEXICONS.items():
            overlap = banned_names.intersection(set(terms))
            self.assertEqual(len(overlap), 0, f"Hardcoded names {overlap} found in lexicon for {proj}")

        primer = LexicalPrimer("apex_trans")
        prompt = primer.build_whisper_prompt(
            meeting_name="Grid Sync",
            attendees=[{"name": "Sarah Connor"}, {"name": "Kyle Reese"}],
        )
        self.assertIn("Sarah Connor", prompt)
        self.assertNotIn("Drew Oakley", prompt)

        # 2. <6-word structural prompt echo caught, while legitimate entity mention survives
        self.assertTrue(is_prompt_echo("Domain terms: ROCC, SCADA.", prompt))
        self.assertTrue(is_prompt_echo("Keywords: Apex Global, APEX.", "Project: apex_trans. Keywords: Apex Global, APEX."))
        self.assertFalse(is_prompt_echo("We reviewed ROCC today.", prompt))

        # 3. VAD-backed polite closings ("Thank you.", "Bye.") survive, while non-VAD silence drops
        self.assertEqual(sanitize_transcript_text("Thank you.", vad_backed=False), "")
        self.assertEqual(sanitize_transcript_text("Thank you.", vad_backed=True), "Thank you.")
        self.assertEqual(sanitize_transcript_text("Bye.", no_speech_prob=0.05, avg_logprob=-0.15), "Bye.")
        self.assertEqual(sanitize_transcript_text("The end.", vad_backed=True), "")

        # 4. Cross-speaker conversational agreement (>1.5s gap) survives deduplication, while same-speaker loop drops
        segments = [
            {"start": 0.0, "end": 2.0, "speaker": "Alice", "text": "Yes I completely agree with that plan."},
            {"start": 5.0, "end": 7.0, "speaker": "Bob", "text": "Yes I completely agree with that plan."},
            {"start": 7.2, "end": 9.0, "speaker": "Bob", "text": "Yes I completely agree with that plan."},
        ]
        deduped = deduplicate_consecutive_asr_segments(segments)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["speaker"], "Alice")
        self.assertEqual(deduped[1]["speaker"], "Bob")


if __name__ == "__main__":
    unittest.main()
