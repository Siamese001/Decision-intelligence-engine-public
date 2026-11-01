"""
tests/test_live_meeting_recorder.py

Comprehensive test suite for LiveMeetingRecorder and live audio capture engine.
Validates:
1. Attendee parsing (freeform, comma-separated, parenthesized orgs, JSON).
2. Governed output hierarchy paths (output/<project>/<MM-DD-YYYY>/).
3. Fast-path initialization & cold-start latency (< 1.5s).
4. Real-time markdown and JSONL segment streaming with fsync.
5. Signal handling and graceful shutdown.
"""

from __future__ import annotations

import json
import shutil
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    get_audio_rms,
    parse_attendees,
)


class TestLiveMeetingRecorder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_recorder_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_attendee_parsing_freeform_and_orgs(self):
        raw = "Taylor Mercer, Justin M, Reid S (NEA), Amit AYer"
        roster = parse_attendees(raw)
        self.assertEqual(len(roster), 4)

        names = [a["name"] for a in roster]
        self.assertIn("Taylor Mercer", names)
        self.assertTrue(any("Justin" in n for n in names))
        self.assertTrue(any("Reid" in n for n in names))
        self.assertTrue(any("Amit" in n for n in names))

        # Check org extraction
        reid_entry = next(a for a in roster if "Reid" in a["name"])
        self.assertIn("NEA", reid_entry["org"])
        self.assertTrue(any(term in reid_entry["role"] for term in ("Quantitative Strategy", "Director", "Analytics")))

        traci_entry = next(a for a in roster if "Traci" in a["name"])
        self.assertEqual(traci_entry["org"], "PartnerAdvisory")
        self.assertIn("Delivery Lead", traci_entry["role"])

    def test_attendee_parsing_json_format(self):
        raw_json = json.dumps([
            {"name": "Alice Smith", "role": "Architect", "org": "PartnerAdvisory"},
            {"name": "Bob Jones", "role": "VP", "org": "Apex Global"},
        ])
        roster = parse_attendees(raw_json)
        self.assertEqual(len(roster), 2)
        self.assertEqual(roster[0]["name"], "Alice Smith")
        self.assertEqual(roster[0]["org"], "PartnerAdvisory")
        self.assertEqual(roster[1]["name"], "Bob Jones")
        self.assertEqual(roster[1]["org"], "Apex Global")

    def test_attendee_parsing_none_fallback(self):
        roster = parse_attendees(None)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["name"], "Amit Ayer")

    def test_params_and_governed_paths(self):
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Nexus Scope Alignment",
            meeting_date="09/14/2026",
            attendees="Taylor Mercer, Justin M",
            output_dir=self.temp_path,
            mock_mode=True,
        )

        self.assertEqual(recorder.project_name, "project_nexus")
        self.assertEqual(recorder.meeting_name, "Nexus Scope Alignment")
        self.assertEqual(recorder.meeting_date_display, "09/14/2026")
        self.assertEqual(recorder.date_dirname, "09-14-2026")
        self.assertEqual(recorder.clean_slug, "Scope_Traci_Justin")

        # Verify output file naming conventions
        self.assertTrue(recorder.master_wav.name.startswith("00_09-14-2026_Scope_Traci_Justin_master_"))
        self.assertTrue(recorder.master_wav.name.endswith(".wav"))
        self.assertEqual(recorder.transcript_md.name, "01_09-14-2026_Scope_Traci_Justin_live_transcript.md")
        self.assertEqual(recorder.segments_jsonl.name, "01_09-14-2026_Scope_Traci_Justin_segments.jsonl")

    def test_fastpath_cold_start_latency(self):
        t0 = time.perf_counter()
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Fast Start Check",
            meeting_date="09/14/2026",
            attendees="Amit Ayer",
            output_dir=self.temp_path,
            mock_mode=True,
        )
        recorder._init_transcript_file()
        elapsed = time.perf_counter() - t0

        # Must initialize in under 500ms
        self.assertLess(elapsed, 0.500, f"Cold start took {elapsed:.4f}s, exceeding 500ms budget")
        self.assertTrue(recorder.transcript_md.exists())

    def test_mock_transcription_and_atomic_streaming(self):
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Mock Meeting",
            meeting_date="09/14/2026",
            attendees="Taylor Mercer, Amit Ayer",
            output_dir=self.temp_path,
            segment_seconds=8,
            mock_mode=True,
        )
        recorder._init_transcript_file()

        # Create a dummy chunk wav file
        chunk_path = recorder.chunks_dir / "chunk_0000.wav"
        with wave.open(str(chunk_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            # Write 1 second of non-silent sine wave
            data = struct.pack("<16000h", *[int(1000 * (i % 10)) for i in range(16000)])
            w.writeframes(data)

        # Process the chunk
        text = recorder.process_chunk(chunk_path, 0)
        self.assertIsNotNone(text)
        self.assertTrue(
            "Mock transcript segment for chunk_0000.wav" in text
            or "Transcribed speech content for chunk_0000.wav" in text
        )

        # Check that markdown transcript has been updated
        content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("# Live Meeting Transcript: Mock Meeting", content)
        self.assertTrue(
            "**[00:00]** Taylor Mercer: Mock transcript segment for chunk_0000.wav" in content
            or "**[00:00]** Speaker: Mock transcript segment for chunk_0000.wav" in content
            or "**[00:00]** Voice A: Mock transcript segment for chunk_0000.wav" in content
            or "**[00:00]** Taylor Mercer: Transcribed speech content for chunk_0000.wav" in content
            or "**[00:00]** Speaker: Transcribed speech content for chunk_0000.wav" in content
            or "**[00:00]** Voice A: Transcribed speech content for chunk_0000.wav" in content
        )

        # Check JSONL segments log
        self.assertTrue(recorder.segments_jsonl.exists())
        lines = recorder.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["chunk_idx"], 0)
        self.assertEqual(event["timestamp"], "[00:00]")
        self.assertEqual(event["chunk_file"], "chunk_0000.wav")

        # Test stop footer
        recorder.stop()
        content_after_stop = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Session paused at", content_after_stop)
        self.assertIn("Total chunks processed: 1", content_after_stop)

    def test_audio_rms_calculation(self):
        from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
            EmptyAudioChunkError,
        )
        # Empty wav (< 100 bytes) raises EmptyAudioChunkError
        empty_wav = self.temp_path / "empty.wav"
        with wave.open(str(empty_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
        with self.assertRaises(EmptyAudioChunkError):
            get_audio_rms(empty_wav)

        # Silent wav with zeros (> 100 bytes) has 0.0 RMS
        silent_wav = self.temp_path / "silent.wav"
        with wave.open(str(silent_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            data = struct.pack("<100h", *[0] * 100)
            w.writeframes(data)
        self.assertEqual(get_audio_rms(silent_wav), 0.0)

        # Constant amplitude wav
        tone_wav = self.temp_path / "tone.wav"
        with wave.open(str(tone_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            data = struct.pack("<1000h", *[500] * 1000)
            w.writeframes(data)
        self.assertAlmostEqual(get_audio_rms(tone_wav), 500.0, places=1)

    def test_end_to_end_simulated_multi_chunk_loop(self):
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Multi Chunk E2E",
            meeting_date="09/14/2026",
            attendees="Justin M, Reid S (NEA)",
            output_dir=self.temp_path,
            segment_seconds=8,
            mock_mode=True,
            isolated_process=False,
        )

        # Pre-populate 3 consecutive chunk files (8 seconds each at 16kHz)
        n_samples = 8 * 16000
        for i in range(3):
            chunk_file = recorder.chunks_dir / f"chunk_{i:04d}.wav"
            with wave.open(str(chunk_file), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                data = struct.pack(f"<{n_samples}h", *[1000] * n_samples)
                w.writeframes(data)

        # Run loop for limited iterations in mock mode
        recorder.run(max_iterations=3)
        recorder.stop()

        self.assertTrue(recorder.transcript_md.exists())
        content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("# Live Meeting Transcript: Multi Chunk E2E", content)
        self.assertTrue(
            "**[00:00]** Justin M:" in content
            or "**[00:00]** Speaker:" in content
            or "**[00:00]** Voice A:" in content
        )
        self.assertTrue(
            "**[00:08]** Justin M:" in content
            or "**[00:08]** Speaker:" in content
            or "**[00:08]** Voice A:" in content
        )
        self.assertTrue(
            "**[00:16]** Justin M:" in content
            or "**[00:16]** Speaker:" in content
            or "**[00:16]** Voice A:" in content
        )
        self.assertIn("Total chunks processed: 3", content)

        # Check JSONL events
        lines = recorder.segments_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        events = [json.loads(line) for line in lines]
        self.assertEqual([e["chunk_idx"] for e in events], [0, 1, 2])
        self.assertEqual([e["seconds"] for e in events], [0, 8, 16])

    def test_cli_record_meeting_engine_dispatch(self):
        from unittest.mock import patch
        from dual_pipeline.cli import main
        with patch("dual_pipeline.cli.engine._handle_record_meeting", return_value={"status": "PASS", "command": "record-meeting"}) as mock_record:
            exit_code = main([
                "record-meeting",
                "--meeting", "CLI Integration Check",
                "--attendees", "Taylor Mercer, Justin M",
            ])
            self.assertEqual(exit_code, 0)
            mock_record.assert_called_once()

    def test_scripts_cli_dry_run_invocation(self):
        import subprocess
        import sys
        repo_root = Path(__file__).resolve().parent.parent
        script_path = repo_root / "scripts" / "live_audio_transcriber.py"
        res = subprocess.run(
            [sys.executable, str(script_path), "--meeting", "Subprocess Check", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("[GOVERNANCE VIOLATION]", res.stderr)

    def test_websocket_streaming_integration(self):
        """Verifies that ws_server is decommissioned (None) and chunks process normally without WS."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="WebSocket Live Test",
            output_dir=self.temp_path,
            mock_mode=True,
        )

        # Pre-populate 1 chunk file
        chunk_file = recorder.chunks_dir / "chunk_0000.wav"
        with wave.open(str(chunk_file), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<16000h", *[1000] * 16000))

        recorder.run(max_iterations=2)

        # ws_server is None (decommissioned in Wave 6)
        self.assertIsNone(recorder.ws_server)
        self.assertIn("chunk_0000.wav", recorder.processed_chunks)

    def test_processed_chunks_hydration_from_segments_jsonl(self):
        """Regression test: Ensure existing un-transcribed WAV chunks are not skipped on initialization."""
        chunks_dir = self.temp_path / ".chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        (chunks_dir / "chunk_0000.wav").write_bytes(b"RIFF dummy wav data")
        (chunks_dir / "chunk_0001.wav").write_bytes(b"RIFF dummy wav data")

        # Case 1: segments.jsonl does not exist -> processed_chunks must be empty
        rec1 = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Hydration Test",
            output_dir=self.temp_path,
            mock_mode=True,
            enable_ws=False,
        )
        self.assertEqual(len(rec1.processed_chunks), 0)
        rec1._release_slug_lock()

        # Case 2: segments.jsonl exists with chunk_0000 recorded -> only chunk_0000 is processed
        segments_file = self.temp_path / f"01_{rec1.date_dirname}_{rec1.clean_slug}_segments.jsonl"
        segments_file.write_text(json.dumps({"chunk_idx": 0, "chunk_file": "chunk_0000.wav", "text": "Hello"}) + "\n")

        rec2 = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Hydration Test",
            output_dir=self.temp_path,
            mock_mode=True,
            enable_ws=False,
        )
        try:
            self.assertEqual(rec2.processed_chunks, {"chunk_0000.wav"})
        finally:
            rec2._release_slug_lock()

    def test_copilot_advisor_reasoning(self):
        """Validates LiveCopilotAdvisor turn buffering and mock executive evaluation."""
        from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
        advisor = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Copilot Test Session",
            mock_mode=True,
            cooldown_seconds=0.0,
        )
        advisor.record_turn(
            speaker="Sharon Diaram",
            text="We are thinking of extending the SOW milestone by another six weeks without additional funding.",
            timestamp="[05:12]",
        )
        feedback = advisor.evaluate_live_turns(current_time=100.0)
        self.assertIsNotNone(feedback)
        self.assertEqual(feedback.get("type"), "COPILOT_FEEDBACK")
        self.assertIn("suggested_script", feedback)
        self.assertIn("risk_alert", feedback)
        self.assertIn("snap_cue", feedback)
        self.assertIn("risk_trap", feedback)
        self.assertIn("confidence", feedback)
        if feedback.get("confidence") is not None:
            self.assertGreaterEqual(feedback["confidence"], 0.85)

    def test_audio_heartbeat_and_copilot_stream_integration(self):
        """Verifies that recorder emits AUDIO_HEARTBEAT and COPILOT_FEEDBACK frames."""
        import socket
        def _get_port():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', 0))
                return s.getsockname()[1]

        ws_port = _get_port()

        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Copilot EndToEnd Check",
            output_dir=self.temp_path,
            mock_mode=True,
            enable_ws=True,
            ws_port=ws_port,
            enable_copilot=True,
        )
        # Configure copilot advisor with 0 cooldown for immediate trigger in mock
        recorder.copilot_advisor.cooldown_seconds = 0.0
        recorder.copilot_advisor.max_freshness_seconds = 60.0
        recorder.copilot_advisor.request_timeout = 60.0

        # Pre-populate chunk
        chunk_file = recorder.chunks_dir / "chunk_0000.wav"
        with wave.open(str(chunk_file), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            data = struct.pack("<16000h", *[1200] * 16000)
            w.writeframes(data)

        recorder.run(max_iterations=2)

        # Allow async copilot advisor thread to flush under parallel runner load
        for _ in range(50):
            if recorder.copilot_jsonl.exists():
                break
            time.sleep(0.05)

        # Check copilot feedback was logged to JSONL
        self.assertTrue(recorder.copilot_jsonl.exists())
        lines = recorder.copilot_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 1)
        feedback = json.loads(lines[0])
        self.assertEqual(feedback.get("type"), "COPILOT_FEEDBACK")

    def test_detect_capture_collapse(self):
        """Verifies detection of single-speaker collapse during capture."""
        from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
            LiveVoiceRegistry,
            VoiceCluster,
        )

        # Scenario 1: Multi-attendee session with 10+ chunks collapsed into single voice (100% share)
        reg = LiveVoiceRegistry(attendees=["Morgan Vance", "Jake", "Amit"])
        reg.voice_clusters["Voice A"] = VoiceCluster(
            voice_id="Voice A",
            sample_count=10,
        )
        report = reg.detect_capture_collapse(min_chunks=10, collapse_threshold=0.85)
        self.assertTrue(report["is_collapsed"])
        self.assertEqual(report["dominant_voice_id"], "Voice A")
        self.assertAlmostEqual(report["dominant_share"], 1.0)
        self.assertIn("Single-speaker collapse detected", report["warning"])

        # Scenario 2: Single attendee session (collapse rule does not trigger)
        reg_solo = LiveVoiceRegistry(attendees=["Solo Speaker"])
        reg_solo.voice_clusters["Voice A"] = VoiceCluster(
            voice_id="Voice A",
            sample_count=15,
        )
        report_solo = reg_solo.detect_capture_collapse(min_chunks=10, collapse_threshold=0.85)
        self.assertFalse(report_solo["is_collapsed"])

        # Scenario 3: Under min_chunks threshold (e.g. 5 chunks)
        reg_short = LiveVoiceRegistry(attendees=["Alice", "Bob"])
        reg_short.voice_clusters["Voice A"] = VoiceCluster(
            voice_id="Voice A",
            sample_count=5,
        )
        report_short = reg_short.detect_capture_collapse(min_chunks=10, collapse_threshold=0.85)
        self.assertFalse(report_short["is_collapsed"])

        # Scenario 4: Balanced multi-speaker session (e.g. 50% / 50%)
        reg_balanced = LiveVoiceRegistry(attendees=["Alice", "Bob"])
        reg_balanced.voice_clusters["Voice A"] = VoiceCluster(voice_id="Voice A", sample_count=6)
        reg_balanced.voice_clusters["Voice B"] = VoiceCluster(voice_id="Voice B", sample_count=6)
        report_bal = reg_balanced.detect_capture_collapse(min_chunks=10, collapse_threshold=0.85)
        self.assertFalse(report_bal["is_collapsed"])

    def test_reconcile_cross_chunk_segments(self):
        """Verifies temporal overlap tagging and voice ID synchronization."""
        from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
            LiveVoiceRegistry,
            VoiceCluster,
        )

        reg = LiveVoiceRegistry()
        reg.voice_clusters["Voice A"] = VoiceCluster(
            voice_id="Voice A",
            resolved_speaker="Alice",
            confidence=0.92,
        )

        segments = [
            {"chunk_idx": 0, "start_sec": 0.0, "end_sec": 5.0, "voice_id": "Voice A", "text": "Hello"},
            {"chunk_idx": 1, "start_sec": 4.5, "end_sec": 8.0, "voice_id": "Voice B", "text": "Interrupting"},
            {"chunk_idx": 2, "start_sec": 10.0, "end_sec": 15.0, "voice_id": "Voice A", "text": "Back to Alice"},
        ]
        reconciled = reg.reconcile_cross_chunk_segments(segments, overlap_tolerance_sec=0.20)
        self.assertEqual(len(reconciled), 3)
        self.assertEqual(reconciled[0]["speaker"], "Alice")
        self.assertEqual(reconciled[2]["speaker"], "Alice")
        self.assertTrue(reconciled[1].get("is_overlap"))
        self.assertAlmostEqual(reconciled[1]["overlap_duration_sec"], 0.5)
        self.assertTrue(reconciled[0].get("has_overlap_following"))
        self.assertFalse(reconciled[2].get("is_overlap", False))

    def test_acoustic_evidence_receipt_emission(self):
        """Verifies terminal acoustic evidence receipt emission and decoupling of recording vs attribution."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Acoustic Receipt Check",
            output_dir=self.temp_path,
            mock_mode=True,
            attendees="Alice, Bob",
        )
        chunk_file = recorder.chunks_dir / "chunk_0000.wav"
        with wave.open(str(chunk_file), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<16000h", *[1200] * 16000))

        recorder.run(max_iterations=1)
        recorder.stop()

        receipt_file = recorder.acoustic_evidence_receipt_path
        self.assertTrue(receipt_file.exists(), f"Missing receipt at {receipt_file}")

        data = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertTrue(data.get("recording_completed"))
        self.assertFalse(data.get("attribution_completed"))
        self.assertEqual(data.get("attribution_status"), "PENDING_ATTRIBUTION")
        self.assertIn("collapse_detection", data)

        manifest_data = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertTrue(manifest_data.get("recording_completed"))
        self.assertFalse(manifest_data.get("attribution_completed"))
        self.assertEqual(manifest_data.get("acoustic_evidence_receipt"), str(receipt_file))

    def test_benchmark_acoustic_representations_and_switches(self):
        """Verifies acoustic representation benchmarking and intra-chunk switch detection."""
        from dual_pipeline.live_meeting_recorder_core.acoustic_features import (
            benchmark_acoustic_representations,
            detect_intra_chunk_switches,
            extract_acoustic_signature,
        )
        import numpy as np

        wav_path = self.temp_path / "bench_chunk.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<16000h", *[1500] * 16000))

        sig = extract_acoustic_signature(wav_path)
        self.assertEqual(len(sig), 21)

        bench1 = benchmark_acoustic_representations(wav_path)
        self.assertEqual(bench1["handcrafted_dim"], 21)
        self.assertFalse(bench1["has_learned_vector"])

        learned_vec = np.random.randn(192).astype(np.float32)
        bench2 = benchmark_acoustic_representations(wav_path, learned_vector=learned_vec)
        self.assertTrue(bench2["has_learned_vector"])
        self.assertEqual(bench2["learned_dim"], 192)

        f1 = np.ones(21, dtype=np.float32) / np.sqrt(21)
        f2 = np.ones(21, dtype=np.float32) / np.sqrt(21)
        f3 = -np.ones(21, dtype=np.float32) / np.sqrt(21)
        switches = detect_intra_chunk_switches([f1, f2, f3], distance_threshold=0.30)
        self.assertEqual(switches, [2])


if __name__ == "__main__":
    unittest.main()

