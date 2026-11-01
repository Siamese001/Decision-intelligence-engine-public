"""
tests/end_to_end/test_mock_offline_real_execution_wave3_e2e.py

Class B E2E Acceptance Test Suite for Wave 3:
Replace fabricated computation and execution across retrieval, evaluation,
recording, transcription, OCR, document extraction, and graph lookups.

Invariants Verified:
1. Reranker/NLI: Executes real model or fails closed with RERANKER_UNAVAILABLE.
2. Embedding & Vector evaluation: MOCK_VECTORS_PROHIBITED and real PII scanning.
3. Judge Orchestration: MOCK_JUDGE_PROHIBITED on mock=True.
4. Spark Retrieval Evaluation: Real query properties determine all 11 gates.
5. Audio Proof of Capture: Real PCM byte rate and acoustic RMS without synthetic passes.
6. Audio Transcription: MOCK_ASR_PROHIBITED across chunk transcriber and base transcriber.
7. Local Model Runtime: MOCK_MODEL_PROHIBITED on mock_mode=True.
8. Visual Image Extraction: MOCK_OCR_PROHIBITED and authentic SVG/DrawIO extraction.
9. XLSX Document Extraction: Real openpyxl parsing without silent CSV fallback.
10. GraphDB Masking: Real SQLite directory lookup and verifiable operation provenance.
11. Audio Source Classification: Truthfully distinguishes live capture from recorded ASR.
"""
from __future__ import annotations

import io
import math
import os
import sqlite3
import struct
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
from dual_pipeline.retrieval.bge_reranker_service import BGERerankerService
from dual_pipeline.runtime.model_runtime import LocalModelRuntime
from dual_pipeline.spark.retrieval_eval_spark import SparkRetrievalReleaseEvaluator
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_legacy_msg, parse_legacy_ppt
from dual_pipeline.transcript_pipeline_core.graphdb_masking import GraphDBMasker
from dual_pipeline.transcript_pipeline_core.source_modality import distinguish_audio_capture_type


def _create_test_wav(
    path: Path,
    duration_sec: float = 1.0,
    sample_rate: int = 16000,
    freq_hz: float = 440.0,
    amplitude: float = 4000.0,
    silent: bool = False,
) -> Path:
    """Helper to synthesize authentic WAV PCM file for verification."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            if silent:
                sample_val = 0
            else:
                t = float(i) / sample_rate
                sample_val = int(amplitude * math.sin(2.0 * math.pi * freq_hz * t))
            frames.extend(struct.pack("<h", max(-32768, min(32767, sample_val))))
        wf.writeframes(bytes(frames))
    return path


class TestMockOfflineRealExecutionWave3E2E:
    """Wave 3 Acceptance E2E Suite: Genuine execution and fail-closed dependency bounds."""

    def test_reranker_executes_real_model_or_reports_unavailable(self, monkeypatch):
        """Reranker service must run FlagReranker or raise RERANKER_UNAVAILABLE; never return fake 1.0/0.5."""
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        query = "What is the strategic plan for cloud deployment?"
        doc = "The cloud deployment architecture relies on local Apple Silicon Metal nodes."
        try:
            score = BGERerankerService.compute_rerank_score(query, doc)
            assert isinstance(score, float)
            assert not math.isnan(score)
        except RuntimeError as exc:
            assert "RERANKER_UNAVAILABLE" in str(exc)

    def test_master_vector_evaluator_prohibits_mock_and_detects_pii(self):
        """MasterVectorEvaluator prohibits mock=True and runs real PII detection."""
        with pytest.raises((RuntimeError, ValueError), match="MOCK_VECTORS_PROHIBITED"):
            MasterVectorEvaluator(mock=True)

        evaluator = MasterVectorEvaluator(mock=False)
        assert evaluator.mock is False

        # Verify real PII scanning works
        clean_text = "Clean engineering report on architecture L0 to L6."
        assert evaluator.verify_pii_hygiene(clean_text) is True

    def test_decomposed_multi_judge_prohibits_mock(self):
        """DecomposedMultiJudge raises MOCK_JUDGE_PROHIBITED if mock=True."""
        with pytest.raises((RuntimeError, ValueError), match="MOCK_JUDGE_PROHIBITED"):
            DecomposedMultiJudge(mock=True)

    def test_spark_retrieval_eval_requires_genuine_query_data(self):
        """SparkRetrievalReleaseEvaluator enforces genuine query properties rather than hardcoded passes."""
        evaluator = SparkRetrievalReleaseEvaluator(mock_mode=False)

        # 1. score_partition_queries does not default rank to 1 when missing
        unranked_query = {"query_id": "q1", "text": "test query"}
        scored = evaluator.score_partition_queries([unranked_query], mock_mode=False)
        assert scored["mrr_sum"] == 0.0
        assert scored["query_count"] == 1

        ranked_query = {"query_id": "q1", "text": "test query", "golden_rank": 1}
        scored_ranked = evaluator.score_partition_queries([ranked_query], mock_mode=False)
        assert scored_ranked["mrr_sum"] == 1.0

        # 2. evaluate_queries_distributed evaluates all 11 gates against genuine query properties
        unverified_queries = [
            {"query_id": "q1", "text": "test 1", "golden_rank": 1},
            {"query_id": "q2", "text": "test 2", "golden_rank": 1},
            {"query_id": "q3", "text": "test 3", "golden_rank": 1},
        ]
        res_unverified = evaluator.evaluate_queries_distributed(unverified_queries)
        assert res_unverified.is_passing is False
        assert res_unverified.gate_results["real_bge_m3"] is False

        # Genuine verified queries pass all gates
        verified_queries = [
            {
                "query_id": f"q{i}",
                "text": f"verified query {i}",
                "golden_rank": 1,
                "is_real_bge": True,
                "pair_binding_hash": "sha256_mock_valid_hex_hash_0000000000",
                "component_verified": True,
                "repeat_stable": True,
                "has_leakage": False,
                "latency_ms": 100.0,
            }
            for i in range(1, 4)
        ]
        res_verified = evaluator.evaluate_queries_distributed(verified_queries)
        assert res_verified.is_passing is True
        assert res_verified.gate_results["real_bge_m3"] is True

    def test_audio_capture_proof_requires_real_pcm(self, tmp_path: Path):
        """AudioCaptureProofVerifier computes genuine metrics on PCM bytes and rejects mock_mode."""
        real_wav = tmp_path / "valid_pcm.wav"
        _create_test_wav(real_wav, duration_sec=1.5, amplitude=4000.0)

        # P1: Real byte rate on actual WAV file
        p1 = AudioCaptureProofVerifier.verify_pcm_byte_rate(
            master_wav=real_wav,
            is_stereo=False,
            min_duration_sec=0.5,
            deadline_sec=2.0,
            mock_mode=False,
        )
        assert p1["proven"] is True
        assert p1["bytes_read"] >= 48000
        assert p1["measured_bps"] > 0

        # P2: Real acoustic variance on actual audio bytes
        raw_bytes = real_wav.read_bytes()
        p2 = AudioCaptureProofVerifier.verify_mic_non_digital_zero(
            pcm_bytes=raw_bytes,
            is_stereo=False,
            mock_mode=False,
        )
        assert p2["proven"] is True
        assert p2["mic_rms"] > 50.0

        # P1 & P2: mock_mode=True fails closed
        with pytest.raises(AudioCaptureUnprovenError) as exc_p1:
            AudioCaptureProofVerifier.verify_pcm_byte_rate(real_wav, mock_mode=True)
        assert exc_p1.value.reason_code == AudioReasonCode.MOCK_AUDIO_PROHIBITED

        with pytest.raises(AudioCaptureUnprovenError) as exc_p2:
            AudioCaptureProofVerifier.verify_mic_non_digital_zero(raw_bytes, mock_mode=True)
        assert exc_p2.value.reason_code == AudioReasonCode.MOCK_AUDIO_PROHIBITED

    def test_audio_transcriber_prohibits_mock_mode(self, tmp_path: Path):
        """Both chunk transcriber and BaseAudioTranscriber prohibit mock mode."""
        dummy_wav = tmp_path / "test.wav"
        _create_test_wav(dummy_wav, duration_sec=0.5)

        with pytest.raises(RuntimeError, match="MOCK_ASR_PROHIBITED"):
            transcribe_meeting_chunk(dummy_wav, mock_mode=True)

        base_transcriber = BaseAudioTranscriber(mock=True)
        with pytest.raises(RuntimeError, match="MOCK_ASR_PROHIBITED"):
            base_transcriber.transcribe(str(dummy_wav))

        with pytest.raises(RuntimeError, match="MOCK_ASR_PROHIBITED"):
            base_transcriber.transcribe_signal(str(dummy_wav))

    def test_local_model_runtime_prohibits_mock(self):
        """LocalModelRuntime raises MOCK_MODEL_PROHIBITED when mock_mode=True."""
        with pytest.raises(RuntimeError, match="MOCK_MODEL_PROHIBITED"):
            LocalModelRuntime(mock_mode=True)

    def test_image_extractor_disallows_mock_and_extracts_svg(self, tmp_path: Path):
        """Image extractor rejects mock_mode and performs genuine structured diagram extraction."""
        svg_file = tmp_path / "arch.svg"
        svg_content = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
            '<rect width="100" height="50" id="box1"/>'
            '<text x="10" y="20">Architecture Component A</text>'
            '</svg>'
        )
        svg_file.write_text(svg_content, encoding="utf-8")

        res = extract_image_visual(svg_file, mock_mode=False)
        assert "Architecture Component A" in res["text"]
        assert len(res["components"]) >= 1

        with pytest.raises(RuntimeError, match="MOCK_OCR_PROHIBITED"):
            extract_image_visual(svg_file, mock_mode=True)

    def test_xlsx_content_loader_requires_openpyxl(self, tmp_path: Path):
        """_load_xlsx requires valid openpyxl and fails closed without silent CSV fallback."""
        dummy_path = tmp_path / "valid.xlsx"
        try:
            import openpyxl  # noqa: F401
            has_openpyxl = True
        except ImportError:
            has_openpyxl = False

        if not has_openpyxl:
            # Without openpyxl, _load_xlsx must fail closed reporting missing dependency,
            # never pretending to parse XLSX as synthetic CSV.
            with pytest.raises(RuntimeError, match="openpyxl is required"):
                _load_xlsx(dummy_path, b"dummy payload")
        else:
            corrupted_path = tmp_path / "corrupted.xlsx"
            with pytest.raises(ValueError, match="Failed to parse valid XLSX workbook"):
                _load_xlsx(corrupted_path, b"not a zip or xlsx file")

    def test_graphdb_masking_with_sqlite_and_provenance(self, tmp_path: Path):
        """GraphDBMasker queries authentic SQLite tables and reports operation provenance."""
        db_file = tmp_path / "canonical.db"
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
        conn.execute(
            "INSERT INTO canonical_graph_nodes VALUES ('n1', 'e1', 'Executive Director', 'Alice Walker', '{}')"
        )
        conn.execute(
            "INSERT INTO canonical_graph_nodes VALUES ('n2', 'e2', 'Principal Architect', 'Bob Vance', '{}')"
        )
        conn.commit()
        conn.close()

        masker = GraphDBMasker(db_path=db_file)
        text = "Alice Walker agreed with Bob Vance on the architectural design."
        masked = masker.mask_payload(text)

        assert "Alice Walker" not in masked
        assert "Bob Vance" not in masked
        assert "Executive Director" in masked or "Principal Architect" in masked

        prov = masker.get_last_provenance()
        assert prov["source"] == "sqlite"
        assert prov["entities_matched"] == 2
        assert prov["cache_misses"] == 1
        assert prov["cache_hits"] == 0

        # Repeated call exercises genuine cache hit
        masked_again = masker.mask_payload(text)
        assert masked_again == masked
        assert masker.cache_hits == 1

    def test_query_swarm_deterministic_scoring_without_sleep(self):
        """CompetitiveQuerySwarm evaluates routes deterministically with zero sleep or random hashes."""
        swarm = CompetitiveQuerySwarm()
        winner = swarm.route_query('find exact match for "Architecture Spec"')
        assert winner["route_id"] == "ROUTE_1_EXACT_MATCH"
        assert winner["confidence"] >= 0.90

        winner_strat = swarm.route_query("strategic decision on risk and priority")
        assert winner_strat["route_id"] == "ROUTE_6_STRATEGIC_W6"
        assert winner_strat["confidence"] >= 0.90

    def test_distinguish_audio_capture_type(self):
        """distinguish_audio_capture_type distinguishes live capture from recorded ASR and non-audio."""
        assert distinguish_audio_capture_type("Meeting notes", {"is_live_capture": True}) == "LIVE_CAPTURE"
        assert distinguish_audio_capture_type("# Live Meeting Transcript:\nDialogue", {}) == "LIVE_CAPTURE"
        assert distinguish_audio_capture_type("", {}, input_ext=".wav") == "RECORDED_AUDIO_ASR"
        assert distinguish_audio_capture_type("", {"source_type": "AUDIO_TRANSCRIBED"}) == "RECORDED_AUDIO_ASR"
        assert distinguish_audio_capture_type("Regular text document", {}, input_ext=".txt") == "NON_AUDIO"
