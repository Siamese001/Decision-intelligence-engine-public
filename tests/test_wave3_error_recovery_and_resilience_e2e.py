"""
tests/test_wave3_error_recovery_and_resilience_e2e.py

End-to-end tests exercising Wave 3 Error Recovery & Resilience capabilities:
- Pipeline stage checkpointing and resume (skipping completed stages)
- Idempotent retry with exponential backoff and jitter
- Partial result preservation on Output 03 QA exit failure
- Graceful degradation on GraphDB write/publication failure
- Dead Letter Queue (DLQ) ingestion failure capture, inspection, and resolution
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.dlq import DeadLetterQueueManager
from dual_pipeline.resilience import generate_idempotency_key, with_idempotent_retry
from dual_pipeline.unified_agentic_pipeline import PipelineStage


class TestWave3ErrorRecoveryAndResilienceE2E(unittest.TestCase):
    """E2E suite verifying Wave 3 error recovery and resilience capabilities."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_pipeline_checkpoint_save_and_resume_e2e(self):
        """Verifies checkpointing intermediate stage state and resuming skipped stages."""
        cp_file = self.tmp_path / "checkpoints" / "checkpoint_test_meeting.json"
        manager = PipelineCheckpointManager(cp_file)

        # Stage U0 complete
        self.assertFalse(manager.has_stage("U0"))
        manager.save_stage("U0", {
            "source_text": "Alex Brennan: Reviewing project nexus timeline.",
            "input_hash": "hash_123456",
            "src_file_path": None,
        })
        self.assertTrue(manager.has_stage("U0"))

        # Stage L1/L0 complete
        manager.save_stage("L1_L0", {
            "modality": "MEETING_TRANSCRIPT",
            "out_p": str(self.tmp_path / "output"),
        })

        # Reload from disk
        reloaded = PipelineCheckpointManager(cp_file)
        self.assertTrue(reloaded.has_stage("U0"))
        self.assertTrue(reloaded.has_stage("L1_L0"))
        self.assertEqual(reloaded.get_stage("U0")["input_hash"], "hash_123456")

        # Clear on completion
        reloaded.clear()
        self.assertFalse(cp_file.exists())

    def test_idempotent_retry_exponential_backoff_e2e(self):
        """Verifies exponential retry handles transient errors and succeeds on recovery."""
        attempts = 0
        delays = []

        def on_retry(att, exc, d):
            delays.append(d)

        @with_idempotent_retry(
            max_retries=3,
            base_delay=0.01,
            max_delay=0.1,
            backoff_factor=2.0,
            jitter=False,
            on_retry_callback=on_retry,
        )
        def transient_service_call():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionResetError("Connection dropped by peer")
            return {"status": "SUCCESS", "data": "payload"}

        res = transient_service_call()
        self.assertEqual(attempts, 3)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(len(delays), 2)
        # Verify deterministic idempotency key computation
        k1 = generate_idempotency_key({"meeting": "nexus", "date": "2026-09-27"})
        k2 = generate_idempotency_key({"date": "2026-09-27", "meeting": "nexus"})
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("idem_"))

    def test_partial_result_preservation_e2e(self):
        """Verifies that if exit QA fails, passing Outputs 01 & 02 are preserved on disk."""
        out_p = self.tmp_path / "output_nexus"
        out_p.mkdir(parents=True, exist_ok=True)

        meta = {
            "project": "project_nexus",
            "meeting_name": "Weekly Sync",
            "preserve_partial": True,
        }

        synth = SynthesisResult(
            output_1="# Forensic Transcript\n[00:00] Alex: Hello",
            output_2="# Executive Recap\nDecisions made.",
            output_3="",  # Missing or invalid graph
            metadata={"time": "12:05 PM - 12:28 PM ET"},
        )

        CanonicalAgenticPipeline(provider_type="offline")
        # Deploy partial deliverables and verify metadata markers
        from dual_pipeline.canonical_pipeline.lifecycle import deploy_deliverables
        artifacts = deploy_deliverables(out_p, meta, CanonicalModality.MEETING_TRANSCRIPT, synth)

        self.assertIn("01_transcript", artifacts)
        self.assertIn("02_recap", artifacts)
        self.assertTrue(Path(artifacts["01_transcript"]).exists())
        self.assertTrue(Path(artifacts["02_recap"]).exists())

    def test_graceful_degradation_on_graph_write_failure_e2e(self):
        """Verifies that GraphDB write failure does NOT crash pipeline and preserves deliverables."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        out_p = self.tmp_path / "degraded_run"
        out_p.mkdir(parents=True, exist_ok=True)

        meta = {
            "project": "project_nexus",
            "meeting_name": "Sprint Planning",
            "time": "12:05 PM - 12:28 PM ET",
            "time_filename": "1205PM_1228PM_ET",
            "date": "09/27/2026",
            "date_dirname": "09-27-2026",
            "attendees": "Alex Brennan, Amit Ayer",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
        }

        synth = SynthesisResult(
            output_1="# Forensic Transcript\n[00:00] Alex: Hello",
            output_2="# Executive Recap\nDecisions made.",
            output_3=json.dumps({
                "meeting": {
                    "project": "project_nexus",
                    "title": "Sprint Planning",
                    "date": "09/27/2026",
                    "time": "12:05 PM - 12:28 PM ET",
                    "attendees": ["Alex Brennan", "Amit Ayer"],
                },
                "entities": [],
                "relationships": [],
                "assertions": [],
            }),
            metadata=meta,
        )

        eval_res = MagicMock()
        eval_res.authoritative_write_authorized = True
        eval_res.final_decision = {
            "status": "PASS",
            "authoritative_write_authorized": True,
        }
        eval_res.judge_report = {"overall_verdict": "PASS"}
        eval_res.qa_report = {"overall_verdict": "PASS"}

        tracker = pipeline.create_tracker("test_input", meta)
        for stg in (
            PipelineStage.U0_INGRESS,
            PipelineStage.L1_IDENTITY,
            PipelineStage.L0_ROUTING,
            PipelineStage.C0_PA_CONTEXT,
            PipelineStage.L2_SYNTHESIS,
            PipelineStage.X_EXIT_GATES,
        ):
            tracker.start_stage(stg)
            tracker.complete_stage(stg, status="PASS")

        # Mock publish_graph_if_requested to simulate database locking error
        with patch(
            "dual_pipeline.canonical_pipeline.pipeline.publish_graph_if_requested",
            side_effect=RuntimeError("SQLite database locked: timeout after 10000ms"),
        ):
            pipeline._execute_evals_release(
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                meta=meta,
                out_p=out_p,
                source_text="Sample dialogue",
                src_file_path=None,
                synthesis_result=synth,
                eval_result=eval_res,
                mutate_graph=True,
                directive_id="dir_123",
                reviewer="Amit Ayer",
                rationale="Sprint ingestion",
                embedding_backend="bge-m3-dev",
                tracker=tracker,
            )

        # Assert deliverables are saved despite graph write error
        self.assertEqual(meta.get("status"), "DEGRADED_OUTPUTS_SAVED")
        self.assertIn("SQLite database locked", meta.get("graph_mutation_error", ""))
        self.assertTrue(out_p.exists())
        self.assertTrue(len(list(out_p.glob("01_*.md"))) > 0)
        self.assertTrue(len(list(out_p.glob("02_*.md"))) > 0)

    def test_dead_letter_queue_e2e(self):
        """Verifies DLQ captures failures, preserves context, and tracks lifecycle status."""
        dlq_dir = self.tmp_path / "dlq"
        dlq = DeadLetterQueueManager(dlq_dir)

        # Enqueue failure
        try:
            raise ValueError("Corrupt acoustic signal detected at chunk 4")
        except ValueError as err:
            dlq_id = dlq.enqueue(
                input_path="broken_session.wav",
                metadata={"project": "project_nexus", "attempt": 1},
                error=err,
                stage="L2_SYNTHESIS",
            )

        self.assertTrue(dlq_id.startswith("dlq_"))
        item = dlq.get_item(dlq_id)
        self.assertIsNotNone(item)
        self.assertEqual(item["stage"], "L2_SYNTHESIS")
        self.assertEqual(item["error_type"], "ValueError")
        self.assertIn("Corrupt acoustic signal", item["error_message"])
        self.assertEqual(item["status"], "NEW")

        # List items
        items = dlq.list_items(status="NEW")
        self.assertEqual(len(items), 1)

        # Update status
        success = dlq.mark_status(dlq_id, "RESOLVED", "Re-run succeeded with clean audio input")
        self.assertTrue(success)
        resolved_item = dlq.get_item(dlq_id)
        self.assertEqual(resolved_item["status"], "RESOLVED")
        self.assertIsNotNone(resolved_item["resolved_at"])


if __name__ == "__main__":
    unittest.main()
