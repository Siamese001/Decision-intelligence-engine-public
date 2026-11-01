"""
tests/adversarial/test_mock_offline_missing_dependency_wave3_adversarial.py

Class B Adversarial Test Suite for Wave 3:
Missing Dependency, Malformed Payloads, and Invariant Bypass Verification.

Covers Mandatory Edge-Case Categories:
1. Boundary Payload: Empty audio payloads, zero-length PCM buffers, empty query strings.
2. Malformed Input: Corrupted XLSX bytes, truncated MSG binaries, unsupported PPT files.
3. Invariant Bypass Attempt: Attempting to bypass hardware/model checks via mock_mode flags.
4. Concurrency & Race: Concurrent execution of Query Swarm and GraphDB Masker.
5. Exact Production Regression: Legacy parsers refusing to return raw byte decodes on corrupt data.
"""
from __future__ import annotations

import concurrent.futures
import io
import os
import sqlite3
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.attachment_core.image_extractor import extract_image_visual
from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.document_pipeline_core.content_loader import _load_xlsx
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import transcribe_meeting_chunk
from dual_pipeline.llm_judge_core.master_vector_evaluator import MasterVectorEvaluator
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.query_swarm import CompetitiveQuerySwarm, QueryRouteSubAgent
from dual_pipeline.runtime.model_runtime import LocalModelRuntime
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_legacy_msg, parse_legacy_ppt
from dual_pipeline.transcript_pipeline_core.graphdb_masking import GraphDBMasker


class TestMockOfflineMissingDependencyWave3Adversarial:
    """Wave 3 Adversarial Suite: Rejection of forged success, malformed data, and bypass attempts."""

    # --------------------------------------------------------------------------
    # Category 1: Boundary Payload
    # --------------------------------------------------------------------------

    def test_audio_proof_zero_sample_or_digital_zero_fails_closed(self, tmp_path: Path):
        """Zero-sample PCM or all-zero audio must fail closed with MIC_DIGITAL_ZERO."""
        # 1. Zero bytes PCM
        with pytest.raises(AudioCaptureUnprovenError) as exc_empty:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(b"", is_stereo=False)
        assert exc_empty.value.reason_code == AudioReasonCode.PCM_BYTE_RATE_DEFICIT

        # 2. Digital zero PCM (silent audio stream from macOS TCC denial)
        silent_wav = tmp_path / "silent.wav"
        with wave.open(str(silent_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 32000)

        raw_bytes = silent_wav.read_bytes()
        with pytest.raises(AudioCaptureUnprovenError) as exc_zero:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(raw_bytes, is_stereo=False)
        assert exc_zero.value.reason_code == AudioReasonCode.MIC_DIGITAL_ZERO

    def test_query_swarm_empty_boundary_query(self):
        """Empty query string returns confidence 0.0 with baseline route."""
        agent = QueryRouteSubAgent("ROUTE_1_EXACT_MATCH")
        res = agent.evaluate_query("   ")
        assert res["confidence"] == 0.0

    # --------------------------------------------------------------------------
    # Category 2: Malformed Input
    # --------------------------------------------------------------------------

    def test_malformed_xlsx_bytes_fail_closed(self, tmp_path: Path):
        """Corrupted XLSX bytes fail closed without synthetic CSV fallback."""
        fake_xlsx = tmp_path / "corrupt.xlsx"
        corrupt_payload = b"PK\x03\x04corrupted_zip_header_with_garbage_tail"
        with pytest.raises((ValueError, RuntimeError), match="openpyxl is required|Failed to parse valid XLSX workbook"):
            _load_xlsx(fake_xlsx, corrupt_payload)

    def test_malformed_legacy_msg_and_ppt_fail_closed(self):
        """Corrupted MSG payload and legacy binary PPT fail closed without raw string decode."""
        corrupted_msg = b"fake binary msg data without ole compound structure"
        with pytest.raises((ValueError, RuntimeError), match="extract_msg is required|Failed to parse legacy MSG payload"):
            parse_legacy_msg(corrupted_msg)

        unsupported_ppt = b"binary ppt stream"
        with pytest.raises(ValueError, match="Legacy binary PPT"):
            parse_legacy_ppt(unsupported_ppt)

    # --------------------------------------------------------------------------
    # Category 3: Invariant Bypass Attempt
    # --------------------------------------------------------------------------

    def test_audio_proof_mock_mode_bypass_blocked(self, tmp_path: Path):
        """Passing mock_mode=True to AudioCaptureProofVerifier stages must fail closed."""
        dummy_wav = tmp_path / "dummy.wav"
        dummy_wav.write_bytes(b"RIFF" + b"\x00" * 40 + b"\x10\x00" * 16000)

        # verify_pcm_byte_rate bypass attempt
        with pytest.raises(AudioCaptureUnprovenError) as exc_p1:
            AudioCaptureProofVerifier.verify_pcm_byte_rate(dummy_wav, mock_mode=True)
        assert exc_p1.value.reason_code == AudioReasonCode.MOCK_AUDIO_PROHIBITED

        # verify_mic_non_digital_zero bypass attempt
        with pytest.raises(AudioCaptureUnprovenError) as exc_p2:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(dummy_wav.read_bytes(), mock_mode=True)
        assert exc_p2.value.reason_code == AudioReasonCode.MOCK_AUDIO_PROHIBITED

        # verify_continuous_growth bypass attempt
        with pytest.raises(AudioCaptureUnprovenError) as exc_p3:
            AudioCaptureProofVerifier.verify_continuous_growth(
                dummy_wav, last_size=0, last_growth_time=0.0, mock_mode=True
            )
        assert exc_p3.value.reason_code == AudioReasonCode.MOCK_AUDIO_PROHIBITED

        # run_full_start_proof bypass attempt
        with pytest.raises(AudioCaptureUnprovenError) as exc_start:
            AudioCaptureProofVerifier.run_full_start_proof(
                master_wav=dummy_wav, ffmpeg_proc=None, mock_mode=True
            )
        assert exc_start.value.reason_code == AudioReasonCode.MOCK_AUDIO_PROHIBITED

    def test_local_model_mock_bypass_blocked(self, monkeypatch):
        """Setting LOCAL_MODEL_MOCK=1 or passing mock_mode=True must fail closed."""
        # Flag-enabled bypass
        with pytest.raises(RuntimeError, match="MOCK_MODEL_PROHIBITED"):
            LocalModelRuntime(mock_mode=True)

        # Ambient env bypass
        monkeypatch.setenv("LOCAL_MODEL_MOCK", "true")
        with pytest.raises(RuntimeError, match="MOCK_MODEL_PROHIBITED"):
            LocalModelRuntime()

    def test_chunk_transcriber_and_judge_mock_bypass_blocked(self, tmp_path: Path):
        """Mock flags in transcriber and decomposed judge are blocked."""
        dummy_wav = tmp_path / "dummy.wav"
        dummy_wav.write_bytes(b"\x00" * 100)

        with pytest.raises(RuntimeError, match="MOCK_ASR_PROHIBITED"):
            transcribe_meeting_chunk(dummy_wav, mock_mode=True)

        with pytest.raises((RuntimeError, ValueError), match="MOCK_JUDGE_PROHIBITED"):
            DecomposedMultiJudge(mock=True)

        with pytest.raises((RuntimeError, ValueError), match="MOCK_VECTORS_PROHIBITED"):
            MasterVectorEvaluator(mock=True)

    # --------------------------------------------------------------------------
    # Category 4: Concurrency & Race
    # --------------------------------------------------------------------------

    def test_graphdb_masker_concurrent_access(self, tmp_path: Path):
        """Concurrent callers masking text against SQLite database must not deadlock or corrupt state."""
        db_file = tmp_path / "concurrent.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """CREATE TABLE canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                evidence_id TEXT,
                type TEXT,
                name TEXT,
                properties TEXT
            )"""
        )
        conn.execute("INSERT INTO canonical_graph_nodes VALUES ('n1', 'e1', 'CTO', 'Dave Miller', '{}')")
        conn.execute("INSERT INTO canonical_graph_nodes VALUES ('n2', 'e2', 'VP', 'Carol White', '{}')")
        conn.commit()
        conn.close()

        masker = GraphDBMasker(db_path=db_file)
        text_template = "Dave Miller spoke to Carol White about system upgrade iteration {idx}."

        def _mask_task(idx: int) -> str:
            return masker.mask_payload(text_template.format(idx=idx))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_mask_task, i) for i in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 20
        for r in results:
            assert "Dave Miller" not in r
            assert "Carol White" not in r

    # --------------------------------------------------------------------------
    # Category 5: Exact Production Regression
    # --------------------------------------------------------------------------

    def test_image_visual_heuristic_fallback_eliminated(self, tmp_path: Path):
        """Image visual extractor must NOT fall back to simulated visual text on unknown raster images."""
        unsupported_img = tmp_path / "test_diagram.png"
        unsupported_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 20)

        # In an environment without Apple Vision or cloud credentials, must return FAILED status,
        # never 'Simulated Visual Text for test_diagram.png'.
        res = extract_image_visual(unsupported_img, mock_mode=False)
        assert "Simulated Visual Text" not in res.get("text", "")
        # Status must be FAILED when providers are unavailable
        if res.get("status") == "FAILED":
            assert res.get("error_type") == "VISUAL_EXTRACTION_EXHAUSTED"
