"""
tests/end_to_end/test_voiceprint_warmstart_and_hitl_resume_e2e.py

Class B E2E lifecycle test verifying:
1. Cross-meeting voiceprint memory warmstart: Meeting 1 enrolls verified voiceprints; Meeting 2
   auto-resolves recurring speakers via cosine similarity (>= 0.82) without hints or manual review.
2. Lossless Stage C0-PA resume: Halted or re-run pipeline preserves resolved_transcript across
   checkpoints with 0 re-computations / 0 Whisper/Pyannote calls in <1s.
3. Independent SQLite WAL integrity check: Fresh read-only connection verifies PRAGMA integrity_check
   and confirms physical on-disk persistence.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)
from dual_pipeline.canonical_pipeline.c0_pa_runner import (
    execute_c0_pa,
    step_c0_pa_with_resume,
)
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.unified_agentic_pipeline import PipelineStage
from dual_pipeline.speaker_attribution_core import AudioSpeakerAttributionEngine


class TestVoiceprintWarmstartAndHitlResumeE2E:
    """E2E verification of cross-meeting acoustic warmstart and lossless C0-PA resume."""

    @pytest.fixture
    def voiceprint_db(self, tmp_path: Path) -> Path:
        db_file = tmp_path / "voiceprint_memory.sqlite"
        return db_file

    def test_voiceprint_warmstart_across_two_meetings(self, voiceprint_db: Path) -> None:
        """
        Meeting 1: Verified turns enroll voiceprints into VoiceprintStore.
        Meeting 2: Neutral dialogue auto-resolves recurring speakers via acoustic matching.
        """
        store = VoiceprintStore(db_path=voiceprint_db)

        # 1. Meeting 1: Distinct acoustic timbres for Alex Sanchez and Speaker_Alpha
        alex_emb_m1 = generate_deterministic_embedding("alex_sanchez_voice_fixture")
        brien_emb_m1 = generate_deterministic_embedding("brien_voice_fixture")

        # Operator verifies Meeting 1 attributions -> Enroll into SQLite WAL store
        res_alex = store.enroll_voiceprint(
            speaker_name="Alex Sanchez",
            embedding=alex_emb_m1,
            project="partner_advisory_PROJ",
            is_verified=True,
            verification_source="hitl_operator_signoff",
        )
        assert res_alex["action"] == "ENROLLED"
        assert res_alex["sample_count"] == 1

        res_brien = store.enroll_voiceprint(
            speaker_name="Speaker_Alpha",
            embedding=brien_emb_m1,
            project="partner_advisory_PROJ",
            is_verified=True,
            verification_source="hitl_operator_signoff",
        )
        assert res_brien["action"] == "ENROLLED"
        assert res_brien["sample_count"] == 1

        # 2. Meeting 2: Fresh meeting with identical acoustic timbres, but NO hints or self-intros
        meeting2_transcript = (
            "## Live Feed\n\n"
            "Speaker 1: Let's review the new database replication topology today.\n"
            "Speaker 2: The read-replica throughput benchmarks are ready for inspection.\n"
            "Speaker 1: Perfect, let's walk through the latency percentiles first.\n"
            "Speaker 2: All p99 latencies remained strictly under fifteen milliseconds.\n"
        )

        meeting2_metadata = {
            "project": "partner_advisory_PROJ",
            "title": "Database Replication Architecture Review",
            "date": "2026-10-15",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "diarization_status": "UNDIARIZED",
            "autonomous_attribution": True,
            "attendees": [
                {"name": "Alex Sanchez", "role": "Lead Architect", "org": "PartnerAdvisory"},
                {"name": "Speaker_Alpha", "role": "Database Engineer", "org": "PartnerAdvisory"},
            ],
            "voiceprint_db_path": str(voiceprint_db),
            # Cluster embeddings matching enrolled timbres (with tiny realistic variance)
            "cluster_embeddings": {
                "Speaker 1": [x + 0.002 for x in alex_emb_m1],
                "Speaker 2": [x + 0.001 for x in brien_emb_m1],
            },
        }

        engine = AudioSpeakerAttributionEngine()
        result = engine.analyze_and_resolve(
            transcript=meeting2_transcript,
            metadata=meeting2_metadata,
            autonomous_if_decisive=True,
        )

        # Confirm autonomous resolution succeeded via acoustic voiceprint matching
        assert len(result.unmapped_speakers) == 0
        assert result.speaker_map.get("Speaker 1") == "Alex Sanchez"
        assert result.speaker_map.get("Speaker 2") == "Speaker_Alpha"

        # Confirm resolved transcript replaces generic speaker labels with mapped names
        resolved_text = result.resolved_transcript
        assert "Alex Sanchez" in resolved_text
        assert "Speaker_Alpha" in resolved_text
        assert "Speaker 1" not in resolved_text
        assert "Speaker 2" not in resolved_text

    def test_c0_pa_resume_lossless_preserves_resolved_transcript(
        self, tmp_path: Path, voiceprint_db: Path
    ) -> None:
        """
        Stage C0-PA runs on undiarized transcript, resolves speakers, and checkpoints.
        On --resume, C0-PA completes in <1s with 0 model calls, restoring resolved_transcript.
        """
        chk_file = tmp_path / "pipeline_checkpoint.json"
        out_dir = tmp_path / "output_test"
        out_dir.mkdir(parents=True, exist_ok=True)

        checkpoint = PipelineCheckpointManager(chk_file)
        mock_pipeline = MagicMock()
        mock_tracker = MagicMock()

        raw_transcript = (
            "## Live Feed\n\n"
            "Speaker 1: We are kicking off the deployment wave.\n"
            "Speaker 2: Monitoring is green across all regional clusters.\n"
        )

        meta = {
            "project": "CLOUD_OPS",
            "title": "Deployment Wave Review",
            "date": "2026-10-15",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "diarization_status": "UNDIARIZED",
            "autonomous_attribution": True,
            "attendees": [
                {"name": "Elena Rostova", "role": "Site Reliability Lead", "org": "PartnerAdvisory"},
                {"name": "Marcus Vance", "role": "Infrastructure Architect", "org": "PartnerAdvisory"},
            ],
            # Provide pre-scored voiceprint match
            "voiceprint_matches": {
                "Speaker 1": ("Elena Rostova", 0.94),
                "Speaker 2": ("Marcus Vance", 0.91),
            },
        }

        # First run: execute C0-PA from scratch
        pa_context, source_text = step_c0_pa_with_resume(
            pipeline=mock_pipeline,
            checkpoint=checkpoint,
            should_resume=False,
            modality=CanonicalModality.AUDIO,
            source_text=raw_transcript,
            input_hash="hash_12345",
            meta=meta,
            presentation_path=None,
            out_p=out_dir,
            tracker=mock_tracker,
        )

        assert "Elena Rostova:" in source_text
        assert "Marcus Vance:" in source_text
        assert "Speaker 1:" not in source_text
        assert meta["speaker_map"]["Speaker 1"] == "Elena Rostova"
        assert meta["speaker_map"]["Speaker 2"] == "Marcus Vance"
        assert meta["resolved_transcript"] == source_text

        # Verify checkpoint has stage C0_PA with resolved_transcript
        assert checkpoint.has_stage("C0_PA")
        saved_c0 = checkpoint.get_stage("C0_PA")
        assert saved_c0 is not None
        assert "Elena Rostova:" in saved_c0["resolved_transcript"]

        # Second run: execute with should_resume=True
        # Patch attribution engine to ensure 0 attribution/model calls on resume
        with patch.object(
            AudioSpeakerAttributionEngine, "analyze_and_resolve",
            side_effect=RuntimeError("FAIL: Attribution engine invoked during resume!"),
        ):
            t0 = time.perf_counter()
            fresh_meta = {"project": "CLOUD_OPS"}
            resumed_pa_context, resumed_source_text = step_c0_pa_with_resume(
                pipeline=mock_pipeline,
                checkpoint=checkpoint,
                should_resume=True,
                modality=CanonicalModality.AUDIO,
                source_text=raw_transcript,
                input_hash="hash_12345",
                meta=fresh_meta,
                presentation_path=None,
                out_p=out_dir,
                tracker=mock_tracker,
            )
            elapsed_sec = time.perf_counter() - t0

        # Must execute in < 1 second (< 0.10s expected)
        assert elapsed_sec < 1.0

        # Resumed source text must strictly contain the resolved speaker names
        assert "Elena Rostova:" in resumed_source_text
        assert "Marcus Vance:" in resumed_source_text
        assert "Speaker 1:" not in resumed_source_text
        assert fresh_meta["resolved_transcript"] == resumed_source_text
        assert fresh_meta["speaker_map"]["Speaker 1"] == "Elena Rostova"

    def test_voiceprint_sqlite_wal_integrity_and_readback(self, voiceprint_db: Path) -> None:
        """
        Independent physical verification of voiceprint SQLite database via raw read-only connection.
        Verifies PRAGMA integrity_check and table row counts.
        """
        store = VoiceprintStore(db_path=voiceprint_db)
        emb1 = generate_deterministic_embedding("speaker_charlie")
        emb2 = generate_deterministic_embedding("speaker_dana")

        store.enroll_voiceprint("Charlie Day", emb1, project="INTEGRITY_TEST", is_verified=True)
        store.enroll_voiceprint("Dana Scully", emb2, project="INTEGRITY_TEST", is_verified=True)

        # Independent physical check bypassing store class
        uri = f"file:{voiceprint_db.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            integrity_row = cur.fetchone()
            assert integrity_row is not None
            assert integrity_row[0] == "ok"

            cur.execute("SELECT canonical_name, sample_count, is_verified FROM speaker_voiceprints ORDER BY canonical_name;")
            rows = cur.fetchall()
            assert len(rows) == 2
            assert rows[0] == ("Charlie Day", 1, 1)
            assert rows[1] == ("Dana Scully", 1, 1)
        finally:
            conn.close()
