"""
tests/adversarial/test_refactored_checkpoint_adversarial.py

Adversarial test suite validating checkpoint tampering detection,
corrupted payloads, configuration drift rejection, and sequencing invariant enforcement.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.unified_agentic_pipeline import (
    PipelineSequencingViolationError,
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


class TestRefactoredCheckpointAdversarial(unittest.TestCase):
    def test_checkpoint_tampered_config_hash_rejected(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = UnifiedAgenticPipelineTracker(
                pipeline_name="tamper_test",
                input_path="input.txt",
            )
            tracker.start_stage(PipelineStage.U0_INGRESS)
            tracker.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details={})
            cp_path = tracker.save_checkpoint(PipelineStage.U0_INGRESS, payload={}, checkpoint_dir=tmp_dir)

            # Tamper with the saved checkpoint's config_sha256 on disk
            cp_data = json.loads(Path(cp_path).read_text(encoding="utf-8"))
            cp_data["config_sha256"] = "tampered-sha-value"
            Path(cp_path).write_text(json.dumps(cp_data), encoding="utf-8")

            # Tracker must reject resuming from tampered checkpoint
            can_resume = tracker.can_resume_from_stage(PipelineStage.L1_IDENTITY, checkpoint_dir=tmp_dir)
            self.assertFalse(can_resume, "Must reject resume when checkpoint config hash is tampered")

    def test_corrupted_checkpoint_json_gracefully_handled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cp_file = Path(tmp_dir) / f"checkpoint_{PipelineStage.U0_INGRESS.value}.json"
            cp_file.write_text("{corrupted-json-payload", encoding="utf-8")

            tracker = UnifiedAgenticPipelineTracker(
                pipeline_name="corrupt_test",
                input_path="input.txt",
            )
            loaded = tracker.load_checkpoint(PipelineStage.U0_INGRESS, checkpoint_dir=tmp_dir)
            self.assertIsNone(loaded, "Corrupted checkpoint JSON must return None rather than crashing")

    def test_out_of_order_stage_execution_raises_sequencing_violation(self) -> None:
        tracker = UnifiedAgenticPipelineTracker(
            pipeline_name="sequence_violation_test",
            input_path="input.txt",
        )
        # Attempt to jump straight to L2 without U0, L1, L0, C0-PA
        with self.assertRaises(PipelineSequencingViolationError):
            tracker.start_stage(PipelineStage.L2_SYNTHESIS)

    def test_complete_unstarted_stage_raises_sequencing_violation(self) -> None:
        tracker = UnifiedAgenticPipelineTracker(
            pipeline_name="unstarted_test",
            input_path="input.txt",
        )
        with self.assertRaises(PipelineSequencingViolationError):
            tracker.complete_stage(PipelineStage.U0_INGRESS)

    def test_transcript_checkpoint_dependency_invalidation_on_source_change(self) -> None:
        from dual_pipeline.canonical_pipeline.transcript_resume_binding import build_transcript_resume_binding
        from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
        from dual_pipeline.checkpoint import PipelineCheckpointManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "transcript_checkpoint.json"
            provider = OfflineTranscriptSynthesizer()

            b1 = build_transcript_resume_binding(
                source_text="original transcript content",
                metadata={"project": "test_proj"},
                provider=provider,
                provider_type="offline",
            )
            cp1 = PipelineCheckpointManager(ckpt_path, require_binding=True)
            cp1.bind(b1)
            cp1.save_stage("VALIDATED_SYNTHESIS", {"attempt": 1, "test": "data"})
            self.assertTrue(cp1.has_stage("VALIDATED_SYNTHESIS"))

            # Changed source text must invalidate VALIDATED_SYNTHESIS
            b2 = build_transcript_resume_binding(
                source_text="modified transcript content",
                metadata={"project": "test_proj"},
                provider=provider,
                provider_type="offline",
            )
            self.assertNotEqual(b1["binding_sha256"], b2["binding_sha256"])

            cp2 = PipelineCheckpointManager(ckpt_path, require_binding=True)
            audit = cp2.bind(b2)
            self.assertEqual(audit["reason"], "BINDING_CHANGED")
            self.assertFalse(cp2.has_stage("VALIDATED_SYNTHESIS"), "Source change must invalidate validated synthesis")

    def test_transcript_checkpoint_dependency_invalidation_on_model_revision_change(self) -> None:
        from dual_pipeline.canonical_pipeline.transcript_resume_binding import build_transcript_resume_binding
        from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
        from dual_pipeline.checkpoint import PipelineCheckpointManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            ckpt_path = Path(tmp_dir) / "transcript_checkpoint.json"
            provider = OfflineTranscriptSynthesizer()

            b1 = build_transcript_resume_binding(
                source_text="same source",
                metadata={"project": "test_proj"},
                provider=provider,
                provider_type="offline",
                extraction_revision="v1.0.0",
            )
            cp = PipelineCheckpointManager(ckpt_path, require_binding=True)
            cp.bind(b1)
            cp.save_stage("VALIDATED_SYNTHESIS", {"attempt": 1})

            b2 = build_transcript_resume_binding(
                source_text="same source",
                metadata={"project": "test_proj"},
                provider=provider,
                provider_type="offline",
                extraction_revision="v2.0.0",
            )
            cp_after = PipelineCheckpointManager(ckpt_path, require_binding=True)
            cp_after.bind(b2)
            self.assertFalse(cp_after.has_stage("VALIDATED_SYNTHESIS"), "Model revision change must invalidate synthesis")

    def test_transcript_checkpoint_deliberate_regeneration_bypasses_cache(self) -> None:
        from dual_pipeline.canonical_pipeline.transcript_resume_binding import build_transcript_resume_binding
        from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer

        provider = OfflineTranscriptSynthesizer()
        binding = build_transcript_resume_binding(
            source_text="source text",
            metadata={"project": "test_proj", "regenerate": "02,03"},
            provider=provider,
            provider_type="offline",
        )
        self.assertFalse(binding["reusable"])
        self.assertIn("DELIBERATE_REGENERATION_REQUESTED", binding["reuse_blockers"])

    def test_transcript_checkpoint_corrupt_payload_gracefully_rejected(self) -> None:
        from dual_pipeline.canonical_pipeline.transcript_resume import _validate_cached_transcript
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

        # Missing required fields or truncated payload must fail closed with ValueError
        corrupt_payload = {"synthesis": {}, "evaluation": {}}
        with self.assertRaises(ValueError):
            _validate_cached_transcript(
                corrupt_payload,
                source_text="source text",
                metadata={},
                modality=CanonicalModality.MEETING_TRANSCRIPT,
            )

    def test_transcript_checkpoint_concurrent_lock_isolation(self) -> None:
        from contextlib import ExitStack
        from dual_pipeline.canonical_pipeline.transcript_resume import lock_transcript_source

        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_root = Path(tmp_dir)
            meta1 = {"project": "p1"}
            meta2 = {"project": "p2"}

            with ExitStack() as s1:
                lock_transcript_source(s1, meta1, "source_1", runtime_root)
                self.assertTrue(Path(meta1["_transcript_checkpoint_path"]).parent.is_dir())
                self.assertTrue((runtime_root / "locks").is_dir())

                with ExitStack() as s2:
                    lock_transcript_source(s2, meta2, "source_2", runtime_root)
                    self.assertNotEqual(
                        meta1["_transcript_checkpoint_path"],
                        meta2["_transcript_checkpoint_path"],
                    )


if __name__ == "__main__":
    unittest.main()
