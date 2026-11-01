"""
tests/adversarial/test_fail_fast_release_lifecycle_adversarial.py

Adversarial edge-case and regression tests verifying Wave 5 fail-fast invariants:
1. CanonicalAgenticPipeline.run() raises RuntimeError when mutate_graph=True and QA fails (no mutation attempted).
2. CanonicalAgenticPipeline.run() raises RuntimeError on authoritative mutation failure (no DEGRADED_OUTPUTS_SAVED fallback).
3. PipelineCheckpointManager._load() raises RuntimeError on corrupted or non-dict checkpoint JSON.
4. PipelineCheckpointManager.clear() raises RuntimeError on unlinking failure.
5. PipelineCheckpointManager.save_stage() includes the current checksum-protected version.
6. Checkpoint resume fails fast (ValueError) on incomplete U0, L1_L0, or C0_PA stage data.
7. Checkpoint resume fails fast (ValueError) on input file hash mismatch (stale checkpoint).
8. publish_helper._assert_authoritative_mutation_preconditions raises RuntimeError when release decision is not PASS/APPROVED.
9. publish_helper._assert_authoritative_mutation_preconditions raises RuntimeError when judge evaluation is not PASS.
10. publish_helper._assert_authoritative_mutation_preconditions raises RuntimeError on corrupted JSON.
11. disposition_flow candidate X1 and X2 exit gates raise RuntimeError on validation failure.
12. disposition_flow graph X1/X2/X3 exit gates raise RuntimeError on validation failure.
13. disposition_flow raises RuntimeError if recorder seal fails or lacks event_chain_digest.
14. release_runner._execute_post_publication_maintenance raises RuntimeError on cascade failure under default strict_cascade=True.
15. release_runner._sweep_stale_assertions raises RuntimeError on sweeper failure unless allow_sweeper_failure=True.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    publish_graph_if_requested,
)
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.checkpoint_storage import VERSION
from dual_pipeline.intake_engine_core.disposition_flow import (
    _evaluate_candidate_exit_gates,
    _evaluate_graph_exit_gates,
)
from dual_pipeline.transcript_pipeline_core.release_runner import (
    _execute_post_publication_maintenance,
    _sweep_stale_assertions,
)


class TestPipelineMutationFailFast(unittest.TestCase):
    """Verifies CanonicalAgenticPipeline enforces fail-fast on mutation preconditions."""

    def test_pipeline_mutation_blocks_when_qa_fails(self):
        """Pipeline must raise RuntimeError if mutate_graph=True but QA evaluation failed."""
        pipeline = CanonicalAgenticPipeline(provider_type="mock")
        eval_result = MagicMock()
        eval_result.is_pass = False
        eval_result.final_decision = {"status": "FAIL", "reason": "Gate 1 failed"}
        eval_result.judge_report = {"overall_verdict": "FAIL"}

        synth_mock = MagicMock()
        synth_mock.output_1 = "{}"
        synth_mock.output_2 = "{}"
        synth_mock.output_3 = "{}"

        with patch.object(pipeline, "create_tracker") as mock_tracker, \
             patch.object(pipeline, "_step_u0_with_resume", return_value=("sample transcript text", "hash123", None, None)), \
             patch.object(pipeline, "_step_l1_l0_with_resume") as mock_l1, \
             patch.object(pipeline, "_step_c0_pa_with_resume", return_value=({}, "sample transcript text")), \
             patch("dual_pipeline.canonical_pipeline.pipeline.run_synthesis_loop", return_value=(1, synth_mock, eval_result)), \
             patch("dual_pipeline.canonical_pipeline.pipeline.build_and_validate_intake_package", return_value=Path("/tmp/pkg.json")), \
             patch("dual_pipeline.canonical_pipeline.pipeline.verify_exit_qa"):

            from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
            mock_l1.return_value = (CanonicalModality.MEETING_TRANSCRIPT, Path("/tmp/mock_out"))
            mock_tracker.return_value = MagicMock()

            with pytest.raises(RuntimeError, match="Fail-Fast: Cannot mutate graph because QA/Judge evaluation failed"):
                pipeline.run(
                    input_path=None,
                    metadata={"meeting_name": "TestMeeting", "date": "09/30/2026", "project": "test_proj"},
                    mutate_graph=True,
                    reviewer="operator@test.com",
                )

    def test_pipeline_mutation_failure_raises_without_degraded_state(self):
        """Pipeline mutation failure must raise RuntimeError directly instead of saving DEGRADED_OUTPUTS_SAVED."""
        pipeline = CanonicalAgenticPipeline(provider_type="mock")
        eval_result = MagicMock()
        eval_result.is_pass = True
        eval_result.final_decision = {"status": "PASS"}
        eval_result.judge_report = {"overall_verdict": "PASS"}

        synth_mock = MagicMock()
        synth_mock.output_1 = "{}"
        synth_mock.output_2 = "{}"
        synth_mock.output_3 = "{}"

        with patch.object(pipeline, "create_tracker") as mock_tracker, \
             patch.object(pipeline, "_step_u0_with_resume", return_value=("sample transcript text", "hash123", None, None)), \
             patch.object(pipeline, "_step_l1_l0_with_resume") as mock_l1, \
             patch.object(pipeline, "_step_c0_pa_with_resume", return_value=({}, "sample transcript text")), \
             patch("dual_pipeline.canonical_pipeline.pipeline.run_synthesis_loop", return_value=(1, synth_mock, eval_result)), \
             patch("dual_pipeline.canonical_pipeline.pipeline.verify_exit_qa"), \
             patch("dual_pipeline.canonical_pipeline.pipeline.build_and_validate_intake_package", return_value=(Path("/tmp/pkg.json"), {})), \
             patch("dual_pipeline.canonical_pipeline.pipeline.publish_graph_if_requested", side_effect=Exception("Database connection timed out")):

            from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
            mock_l1.return_value = (CanonicalModality.MEETING_TRANSCRIPT, Path("/tmp/mock_out"))
            mock_tracker.return_value = MagicMock()

            with pytest.raises(RuntimeError, match="Fail-Fast: Authoritative GraphDB mutation failed: Database connection timed out"):
                pipeline.run(
                    input_path=None,
                    metadata={"meeting_name": "TestMeeting", "date": "09/30/2026", "project": "test_proj"},
                    mutate_graph=True,
                    reviewer="operator@test.com",
                )


class TestCheckpointManagerFailFast(unittest.TestCase):
    """Verifies PipelineCheckpointManager fail-fast on corruption, missing fields, and clear failures."""

    def test_checkpoint_load_corrupted_json_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "checkpoint.json"
            ckpt_path.write_text("{ corrupt json ...", encoding="utf-8")
            with pytest.raises(RuntimeError, match="Fail-Fast: Checkpoint file '.*' is corrupted"):
                PipelineCheckpointManager(ckpt_path)

    def test_checkpoint_load_non_dict_json_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "checkpoint.json"
            ckpt_path.write_text("[\"not\", \"a\", \"dict\"]", encoding="utf-8")
            with pytest.raises(RuntimeError, match="Fail-Fast: Checkpoint file '.*' contains non-dict JSON"):
                PipelineCheckpointManager(ckpt_path)

    def test_checkpoint_save_stage_includes_version(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "checkpoint.json"
            mgr = PipelineCheckpointManager(ckpt_path)
            mgr.save_stage("U0", {"source_text": "hello", "input_hash": "abc"})

            data = json.loads(ckpt_path.read_text(encoding="utf-8"))
            assert data.get("version") == VERSION
            assert isinstance(data.get("checksum"), str)
            assert "U0" in data.get("stages", {})

    def test_checkpoint_clear_failure_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "checkpoint.json"
            ckpt_path.write_text("{}")
            mgr = PipelineCheckpointManager(ckpt_path)
            with patch.object(Path, "unlink", side_effect=OSError("Permission denied")), \
                 pytest.raises(RuntimeError, match="Fail-Fast: Failed to clear checkpoint file"):
                mgr.clear()


class TestCheckpointResumeIntegrityFailFast(unittest.TestCase):
    """Verifies pipeline step resumes fail fast on missing fields or input hash mismatch."""

    def test_u0_resume_missing_fields_raises(self):
        pipeline = CanonicalAgenticPipeline(provider_type="mock")
        ckpt = MagicMock()
        ckpt.has_stage.return_value = True
        ckpt.get_stage.return_value = {"source_text": ""}  # missing input_hash and source_text

        with pytest.raises(ValueError, match="Fail-Fast: Resumed U0 checkpoint is incomplete"):
            pipeline._step_u0_with_resume(
                checkpoint=ckpt,
                should_resume=True,
                input_path=None,
                meta={},
                presentation_path=None,
                attachments=None,
                attachment_dir=None,
                signal_envelope=None,
                output_dir="auto",
                mutate_graph=False,
                tracker=MagicMock(),
                progress_callback=None,
            )

    def test_u0_resume_hash_mismatch_raises(self):
        pipeline = CanonicalAgenticPipeline(provider_type="mock")
        ckpt = MagicMock()
        ckpt.has_stage.return_value = True
        ckpt.get_stage.return_value = {
            "source_text": "old content",
            "input_hash": "sha256_old_hash_123",
            "src_file_path": "/tmp/test.txt",
        }

        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("new content modified by operator")
            f_path = f.name

        try:
            with pytest.raises(ValueError, match="Fail-Fast: Input file hash mismatch on resume"):
                pipeline._step_u0_with_resume(
                    checkpoint=ckpt,
                    should_resume=True,
                    input_path=f_path,
                    meta={},
                    presentation_path=None,
                    attachments=None,
                    attachment_dir=None,
                    signal_envelope=None,
                    output_dir="auto",
                    mutate_graph=False,
                    tracker=MagicMock(),
                    progress_callback=None,
                )
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_l1_l0_resume_missing_modality_raises(self):
        pipeline = CanonicalAgenticPipeline(provider_type="mock")
        ckpt = MagicMock()
        ckpt.has_stage.return_value = True
        ckpt.get_stage.return_value = {"out_p": "/tmp/test"}  # missing modality

        with pytest.raises(ValueError, match="Fail-Fast: Resumed L1_L0 checkpoint is incomplete"):
            pipeline._step_l1_l0_with_resume(
                checkpoint=ckpt,
                should_resume=True,
                input_path=None,
                source_text="some text",
                meta={},
                output_dir="auto",
                tracker=MagicMock(),
                progress_callback=None,
            )

    def test_c0_pa_resume_missing_pa_context_raises(self):
        pipeline = CanonicalAgenticPipeline(provider_type="mock")
        ckpt = MagicMock()
        ckpt.has_stage.return_value = True
        ckpt.get_stage.return_value = {"source_text": "hello"}  # missing pa_context

        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
        with pytest.raises(ValueError, match="Fail-Fast: Resumed C0_PA checkpoint is incomplete"):
            pipeline._step_c0_pa_with_resume(
                checkpoint=ckpt,
                should_resume=True,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                source_text="some text",
                input_hash="hash123",
                meta={},
                presentation_path=None,
                out_p=Path("/tmp/out"),
                tracker=MagicMock(),
                progress_callback=None,
            )


class TestPublishHelperPreconditionsFailFast(unittest.TestCase):
    """Verifies publish_helper enforces strict gate status preconditions."""

    def test_release_decision_not_pass_raises(self):
        with tempfile.TemporaryDirectory() as td:
            out_p = Path(td)
            dec_file = out_p / "final_release_decision.json"
            dec_file.write_text(json.dumps({"status": "REJECTED", "overall_verdict": "FAIL"}), encoding="utf-8")
            judge_file = out_p / "llm_judge_evaluation.json"
            judge_file.write_text(json.dumps({"overall_verdict": "PASS"}), encoding="utf-8")

            with pytest.raises(RuntimeError, match="Fail-Fast: GraphDB mutation blocked.*Final release decision status is 'REJECTED'"):
                _assert_authoritative_mutation_preconditions(out_p, {})

    def test_judge_evaluation_not_pass_raises(self):
        with tempfile.TemporaryDirectory() as td:
            out_p = Path(td)
            dec_file = out_p / "final_release_decision.json"
            dec_file.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            judge_file = out_p / "llm_judge_evaluation.json"
            judge_file.write_text(json.dumps({"overall_verdict": "FAIL"}), encoding="utf-8")

            with pytest.raises(RuntimeError, match="Fail-Fast: GraphDB mutation blocked.*Judge evaluation overall_verdict is 'FAIL'"):
                _assert_authoritative_mutation_preconditions(out_p, {})

    def test_corrupted_judge_evaluation_raises(self):
        with tempfile.TemporaryDirectory() as td:
            out_p = Path(td)
            dec_file = out_p / "final_release_decision.json"
            dec_file.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
            judge_file = out_p / "llm_judge_evaluation.json"
            judge_file.write_text("{ truncated json...", encoding="utf-8")

            with pytest.raises(RuntimeError, match="Corrupted judge evaluation file"):
                _assert_authoritative_mutation_preconditions(out_p, {})

    def test_publish_graph_if_requested_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            out_p = Path(td)
            pkg_path = out_p / "intake_package.json"
            pkg_path.write_text("{}", encoding="utf-8")
            dec_file = out_p / "final_release_decision.json"
            dec_file.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")

            with pytest.raises(RuntimeError, match="Fail-Fast: GraphDB mutation blocked"):
                publish_graph_if_requested(
                    mutate_graph=True,
                    intake_pkg_path=pkg_path,
                    metadata={"project": "test_proj"},
                    written={},
                    directive_id="dir-test",
                    reviewer="operator@test.com",
                    rationale="Test",
                    embedding_backend="bge-m3",
                )


class TestDispositionFlowExitGatesFailFast(unittest.TestCase):
    """Verifies disposition_flow exit gates fail fast on non-passing checks."""

    def test_candidate_x1_exit_gate_failure_raises(self):
        dispositions = [{
            "candidate_id": "cand-001",
            "evidence_ids": [],
            "persistent_lane": "canonical",
            "classification_valid": False,  # triggers failure
            "audience_valid": True,
        }]
        bundle = {
            "source_id": "src-001",
            "candidates": [{"candidate_id": "cand-001"}],
        }
        graph_delta = {
            "validation": {"unauthorized_side_effect_count": 0},
        }
        recorder = MagicMock()

        with pytest.raises(RuntimeError, match="Fail-Fast: Candidate X1 exit gate failed for 'cand-001'"):
            _evaluate_candidate_exit_gates(dispositions, bundle, graph_delta, recorder)

    def test_candidate_x2_exit_gate_failure_raises(self):
        dispositions = [{
            "candidate_id": "cand-002",
            "evidence_ids": ["ev-1"],
            "persistent_lane": "none",
            "classification_valid": True,
            "audience_valid": True,
            "disposition": "APPROVED",
            "source_authority_sufficient": False,  # triggers approval authority failure
            "hitl_required": False,
            "hitl_complete": False,
        }]
        bundle = {
            "source_id": "src-002",
            "candidates": [{"candidate_id": "cand-002", "materiality": "HIGH"}],
        }
        graph_delta = {
            "validation": {"unauthorized_side_effect_count": 0},
        }
        recorder = MagicMock()

        with pytest.raises(RuntimeError, match="Fail-Fast: Candidate X2 exit gate failed for 'cand-002'"):
            _evaluate_candidate_exit_gates(dispositions, bundle, graph_delta, recorder)

    def test_candidate_x2_hitl_boundary_blocks_unreviewed_in_approved_lane(self):
        dispositions = [{
            "candidate_id": "cand-003",
            "evidence_ids": ["ev-1"],
            "persistent_lane": "approved",
            "classification_valid": True,
            "audience_valid": True,
            "disposition": "APPROVED",
            "source_authority_sufficient": True,
            "hitl_required": True,
            "hitl_complete": False,  # triggers hitl_boundary_valid failure
        }]
        bundle = {
            "source_id": "src-003",
            "candidates": [{"candidate_id": "cand-003", "materiality": "HIGH"}],
        }
        graph_delta = {
            "validation": {"unauthorized_side_effect_count": 0},
        }
        recorder = MagicMock()

        with pytest.raises(RuntimeError, match="Fail-Fast: Candidate X2 exit gate failed for 'cand-003'"):
            _evaluate_candidate_exit_gates(dispositions, bundle, graph_delta, recorder)

    def test_candidate_x2_hitl_boundary_permits_unreviewed_in_candidate_lane(self):
        dispositions = [{
            "candidate_id": "cand-004",
            "evidence_ids": ["ev-1"],
            "persistent_lane": "candidate",
            "classification_valid": True,
            "audience_valid": True,
            "disposition": "CANDIDATE",
            "source_authority_sufficient": False,
            "hitl_required": True,
            "hitl_complete": False,  # valid: persistent_lane is candidate, not approved
        }]
        bundle = {
            "source_id": "src-004",
            "candidates": [{"candidate_id": "cand-004", "materiality": "MEDIUM"}],
        }
        graph_delta = {
            "validation": {"unauthorized_side_effect_count": 0},
        }
        recorder = MagicMock()

        c_by_id = _evaluate_candidate_exit_gates(dispositions, bundle, graph_delta, recorder)
        self.assertIn("cand-004", c_by_id)

    def test_graph_exit_gates_failure_raises(self):
        bundle = {"source_id": "src-003"}
        graph_delta = {
            "validation": {
                "unauthorized_side_effect_count": 5,  # unauthorized side effects trigger X1 fail
                "candidate_lane_operations": 0,
                "canonical_lane_operations": 0,
            },
            "change_set_id": "cs-001",
            "operation_count": 1,
            "table": "summary",
        }
        recorder = MagicMock()

        with pytest.raises(RuntimeError, match="Fail-Fast: Stage X1 exit gate failed with status 'FAIL'"):
            _evaluate_graph_exit_gates(
                bundle=bundle,
                graph_delta=graph_delta,
                graph_delta_digest="sha256_mock",
                graph_delta_path_name="delta.json",
                schema_and_lineage_valid=False,
                lane_separation_valid=True,
                all_operations_accounted_for=True,
                recorder=recorder,
            )


class TestReleaseRunnerMaintenanceFailFast(unittest.TestCase):
    """Verifies release runner maintenance phases fail fast on errors."""

    def test_cascade_invalidation_failure_raises_under_strict_mode(self):
        pub_res = {"status": "COMPLETE", "receipt_path": None}
        out_p = Path("/tmp/mock_out")
        metadata = {"strict_cascade": True, "meeting_name": "TestMeeting"}
        written = {"03_graph": "/tmp/mock_03.json"}

        with patch("dual_pipeline.transcript_pipeline_core.release_runner.Path.is_file", return_value=True), \
             patch("dual_pipeline.core.read_json", return_value={}), \
             patch("dual_pipeline.staleness_core.intent_extractor.IntentExtractor.extract_from_output_3", side_effect=Exception("Staleness graph parse error")), \
             pytest.raises(RuntimeError, match="Cascade invalidation failed under strict_cascade mode"):
                _execute_post_publication_maintenance(
                    pub_res=pub_res,
                    out_p=out_p,
                    metadata=metadata,
                    written=written,
                    canon_runtime_root=Path("/tmp/rt"),
                    dir_id="dir-001",
                )

    def test_stale_assertion_sweeper_failure_raises(self):
        metadata = {"graph_path": "/tmp/nonexistent_mock_graph.sqlite", "allow_sweeper_failure": False}
        written = {}

        with patch("dual_pipeline.transcript_pipeline_core.release_runner.Path.exists", return_value=True), \
             patch("storage.connection.create_sqlite_conn", side_effect=Exception("Database lock acquisition failure")), \
             pytest.raises(RuntimeError, match="Fail-Fast: AssertionTTLSweeper failed"):
                _sweep_stale_assertions(metadata, written)


if __name__ == "__main__":
    unittest.main()
