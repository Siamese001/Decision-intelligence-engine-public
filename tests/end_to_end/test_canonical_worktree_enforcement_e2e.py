"""
tests/end_to_end/test_canonical_worktree_enforcement_e2e.py

End-to-End lifecycle test verifying Master Architectural Invariant 14:
CanonicalAgenticPipeline worktree isolation enforcement across the full 7-stage pipeline.

Validates that when enforce_worktree is enabled:
1. Stage L0 routes execution to the isolated .worktrees/<slug> container.
2. All 3 deliverables (01 Forensic, 02 Recap, 03 Graph Projection) and receipts
   are materialized strictly within the isolated worktree directory.
3. The root repository output/ directory remains pristine with zero mutations.
4. Returned execution receipt and metadata.json bind worktree_dir and worktree_branch.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from dual_pipeline.core import ROOT


class TestCanonicalWorktreeEnforcementE2E(unittest.TestCase):
    """End-to-End test suite for canonical pipeline worktree isolation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_canonical_pipeline_e2e_lifecycle_worktree_enforcement(self):
        """Exercises full pipeline lifecycle verifying deliverables materialize strictly in isolated worktree."""
        mock_wt_dir = self.test_root / ".worktrees" / "live_standup_e2e"
        mock_wt_output = mock_wt_dir / "output"
        mock_wt_output.mkdir(parents=True, exist_ok=True)
        mock_branch = "feat/live-standup-e2e"

        # Create sample transcript
        sample_transcript = self.test_root / "sample_meeting.txt"
        sample_transcript.write_text(
            "Jen Pan: Morning alignment on pipeline worktree governance.\n"
            "Amit Ayer: We have established zero root repo bypass guarantees across CanonicalAgenticPipeline.\n"
            "Jen Pan: Excellent, let us approve the release and verify receipts.\n",
            encoding="utf-8",
        )

        metadata = {
            "project": "project_nexus",
            "meeting_name": "Standup E2E",
            "date": "09-29-2026",
            "time": "09:00 AM - 09:30 AM ET",
            "enforce_worktree": True,
            "attendees": ["Jen Pan", "Amit Ayer"],
            "attendee_roles_confirmed_by_operator": True,
            "force_rerun": True,
            "interactive_resolver": lambda p: p["options"][0]["role"],
        }

        from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
        from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult

        f1_mock = (
            "# Transcript: Standup E2E\n\n"
            "**Date:** 09-29-2026 | **Time:** 09:00 AM - 09:30 AM ET\n\n"
            "Jen Pan: Good morning everyone, let us begin our morning alignment on pipeline worktree governance across the system.\n\n"
            "Amit Ayer: We have established zero root repo bypass guarantees across CanonicalAgenticPipeline so all deliverables go to worktrees.\n\n"
            "Jen Pan: Excellent progress on worktree isolation, let us approve the release and verify all receipts and gates pass.\n"
        )
        f2_mock = (
            "# Executive Recap: Standup E2E\n\n"
            "## Executive Summary\n"
            "Team aligned on pipeline worktree governance.\n"
        )
        f3_mock = json.dumps({
            "schema_version": "5.7",
            "meeting_id": "meeting-standup-e2e",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_index": {},
            "evidence_only_statement_ids": [],
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "name": "PartnerAdvisory", "canonical_name": "PartnerAdvisory", "type": "org", "description": "Consulting firm"},
                    {"id": "person:amit_ayer", "name": "Amit Ayer", "canonical_name": "Amit Ayer", "type": "person", "description": "Principal Architect"},
                    {"id": "person:jen_pan", "name": "Jen Pan", "canonical_name": "Jen Pan", "type": "person", "description": "Product Manager / Lead Engineer"},
                ],
                "assertion_upserts": [],
            },
        })
        from dual_pipeline.release_policy import build_artifact_manifest, compute_release_evaluation_digest

        synth = SynthesisResult(output_1=f1_mock, output_2=f2_mock, output_3=f3_mock)
        j_rep = {"decision": "PASS", "status": "PASS", "rubric_version": "2.0"}
        eval_digest = compute_release_evaluation_digest(j_rep)
        j_rep["evaluation_receipt_digest"] = eval_digest
        manifest = build_artifact_manifest(
            f1_text=synth.output_1,
            f2_text=synth.output_2,
            f3_text=synth.output_3,
            model="mock-judge",
            reasoning_effort="medium",
            judge_eval_digest=eval_digest,
        )
        final_decision = {
            "status": "PASS",
            "final_pass": True,
            "authoritative_write_authorized": True,
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": True,
            "artifact_manifest": manifest,
        }
        qa_eval = QAEvaluationResult(
            passed=True,
            qa_report={"output_1_pass": True, "output_2_pass": True, "output_3_pass": True},
            judge_report=j_rep,
            final_decision=final_decision,
            authoritative_write_authorized=True,
        )

        mock_x = mock.MagicMock()
        mock_x.execute.return_value = True
        pipeline = CanonicalAgenticPipeline(provider_type="offline", x_gates_override=mock_x)

        with mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
            return_value=False,
        ), mock.patch(
            "dual_pipeline.canonical_pipeline.worktree_inheritance.inherit_worktree_from_metadata",
            return_value=None,
        ), mock.patch(
            "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree",
            return_value=(mock_wt_dir, mock_branch),
        ) as mock_ensure, mock.patch(
            "dual_pipeline.canonical_pipeline.pipeline.run_synthesis_loop",
            return_value=(1, synth, qa_eval),
        ), mock.patch(
            "dual_pipeline.transcript_stages_core.x_gates.XExitGatesEvaluator"
        ) as mock_x_eval:
            mock_x_eval.return_value.execute.return_value = True
            result = pipeline.run(
                input_path=str(sample_transcript),
                metadata=metadata,
                output_dir="auto",
                mutate_graph=False,
            )

            # 1. Verify ensure_live_transcription_worktree was invoked
            mock_ensure.assert_called_once()

            # 2. Verify pipeline completed successfully
            self.assertEqual(result.get("status"), "COMPLETE")
            self.assertEqual(result.get("worktree_dir"), str(mock_wt_dir))
            self.assertEqual(result.get("worktree_branch"), mock_branch)

            # 3. Verify destination output directory is strictly within the isolated worktree
            resolved_output_dir = Path(result["output_dir"])
            self.assertTrue(
                str(resolved_output_dir).startswith(str(mock_wt_dir)),
                f"Expected output_dir {resolved_output_dir} to start with {mock_wt_dir}",
            )
            self.assertEqual(resolved_output_dir.name, "Standup_E2e_Jen_Amit")

            # 4. Verify deliverables exist inside the isolated worktree
            out_01 = Path(result["01_transcript"])
            out_02 = Path(result["02_recap"])
            out_03 = Path(result["03_graph"])
            metadata_file = resolved_output_dir / "metadata.json"
            receipt_file = resolved_output_dir / "pipeline_execution_receipt.json"

            self.assertTrue(out_01.is_file(), f"Output 01 not found at {out_01}")
            self.assertTrue(out_02.is_file(), f"Output 02 not found at {out_02}")
            self.assertTrue(out_03.is_file(), f"Output 03 not found at {out_03}")
            self.assertTrue(metadata_file.is_file(), f"Metadata not found at {metadata_file}")
            self.assertTrue(receipt_file.is_file(), f"Receipt not found at {receipt_file}")

            # 5. Verify metadata.json inside the worktree records worktree isolation telemetry
            persisted_meta = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertTrue(persisted_meta.get("enforce_worktree"))
            self.assertEqual(persisted_meta.get("worktree_dir"), str(mock_wt_dir))
            self.assertEqual(persisted_meta.get("worktree_branch"), mock_branch)

            # 6. Verify pristine root repo guarantee: NO files created under ROOT/output for this meeting
            root_candidate_dir = ROOT / "output" / "project_nexus" / "09-29-2026" / resolved_output_dir.name
            self.assertFalse(
                root_candidate_dir.exists(),
                f"Pristine root repo violated: {root_candidate_dir} exists in primary repository",
            )


if __name__ == "__main__":
    unittest.main()
