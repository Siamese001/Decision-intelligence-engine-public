"""
tests/test_hr360_extraction_recovery.py

Wave 3 Acceptance Test Suite:
Stop deterministic defects from causing full extraction reruns.

Covers:
1. Canonical graph_mutations AST topology and schema validation in heuristic pre-judge.
2. Rejection of unlinked / dangling edges in pre-judge.
3. Empty graph container fail-fast.
4. Proposal wording and evidence preservation without boilerplate corruption.
5. Non-Atlas OCR proposal preservation.
6. Prevention of ungrounded / unsupported relationship injection.
7. Publication-only failure allows zero-extraction resume when bindings match.
8. Changed source text or policy invalidates resume binding.
9. Modified cached artifact invalidates resume.
10. Unresolved model revision fails closed on resume request.
11. Circuit breaker / candidate loop termination on repeated identical failure.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    check_fast_fail_or_circuit_breaker,
)
from dual_pipeline.canonical_pipeline.document_resume import (
    record_document_failure,
    run_synthesis_with_resume,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    check_candidate_loop_termination,
)
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.llm_judge_core.heuristic_pre_judge import (
    check_ast_topology,
    check_schema_entities,
    evaluate_heuristic_pre_judge,
)
from dual_pipeline.transcript_qa_core.graph_ontology_proposals import (
    heal_proposals_and_risks,
)
from dual_pipeline.transcript_qa_core.graph_ontology_signals import (
    _connect_atlas_and_golden,
)


def _build_valid_bundle(source_text: str, output_3: str, attempt: int = 1):
    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
    )
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult

    synthesis = SynthesisResult(None, None, output_3, attempt=attempt)
    judge = {
        "output_3_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
        "status": "PASS",
    }
    judge["evaluation_receipt_digest"] = compute_release_evaluation_digest(judge)
    qa = {
        "passed": True,
        "failures": [],
        "healed_artifacts": {"output_1": "", "output_2": "", "output_3": output_3},
    }
    decision = {
        "status": "PASS",
        "attempt": attempt,
        "authoritative_write_authorized": True,
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
        "cross_artifact_consistency_pass": True,
        "artifact_manifest": build_artifact_manifest(
            source_text, "", output_3, "fixture", "none", judge["evaluation_receipt_digest"]
        ),
    }
    evaluation = QAEvaluationResult(
        passed=True, qa_report=qa, judge_report=judge, final_decision=decision, authoritative_write_authorized=True
    )
    return synthesis, evaluation


class HR360ExtractionRecoveryTests(unittest.TestCase):
    """Wave 3 verification test suite for deterministic recovery and resume binding."""

    def test_canonical_graph_mutations_topology_passes_pre_judge(self) -> None:
        """Valid Output 03 graph_mutations structure passes heuristic pre-judge without rejection."""
        f3_payload = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:hr360", "name": "HR360 Operations"},
                    {"id": "system:workday", "name": "Workday HCM"},
                    {"id": "system:payroll", "name": "Enterprise Payroll"},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "rel-1",
                        "from_id": "org:hr360",
                        "to_id": "system:workday",
                        "relationship": "OPERATES",
                    },
                    {
                        "signal_id": "rel-2",
                        "from_id": "system:workday",
                        "to_id": "system:payroll",
                        "relationship": "INTEGRATES_WITH",
                    },
                ],
            }
        }
        f3_text = json.dumps(f3_payload)
        f1_text = "HR360 Operations operates Workday HCM which integrates with Enterprise Payroll."

        v_schema, err_schema = check_schema_entities(f3_payload)
        self.assertTrue(v_schema, f"Schema validation failed: {err_schema}")

        v_ast, err_ast = check_ast_topology(f3_payload)
        self.assertTrue(v_ast, f"AST topology validation failed: {err_ast}")

        result = evaluate_heuristic_pre_judge(f3_text, f1_text=f1_text)
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["passed_pre_check"])
        self.assertEqual(result["pre_judge_tier"], "PASS_HEURISTIC_PRE_CHECK")

    def test_dangling_or_unsupported_edges_rejected_in_pre_judge(self) -> None:
        """High ratio of unlinked dangling edges or edges with zero entity nodes is rejected."""
        # Scenario A: edges pointing to unknown targets
        bad_topology = {
            "graph_mutations": {
                "entity_upserts": [{"id": "org:hr360", "name": "HR360"}],
                "relationship_signals": [
                    {"from_id": "org:hr360", "to_id": "system:nonexistent-1"},
                    {"from_id": "org:hr360", "to_id": "system:nonexistent-2"},
                ],
            }
        }
        v_ast, err_ast = check_ast_topology(bad_topology)
        self.assertFalse(v_ast)
        self.assertTrue(any("dangling" in e.lower() for e in err_ast))

        # Scenario B: edges present but zero nodes
        zero_nodes_payload = {
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [{"from_id": "a", "to_id": "b"}],
            }
        }
        v_ast_zero, err_ast_zero = check_ast_topology(zero_nodes_payload)
        self.assertFalse(v_ast_zero)
        self.assertTrue(any("0 entity nodes" in e for e in err_ast_zero))

    def test_empty_graph_containers_fail_schema_check(self) -> None:
        """Graph mutations container with zero entities or claims fails schema pre-judge."""
        empty_payload = {
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
            }
        }
        v_schema, err_schema = check_schema_entities(empty_payload)
        self.assertFalse(v_schema)
        self.assertTrue(any("completely empty" in e for e in err_schema))

    def test_grounded_proposal_wording_preserved_without_boilerplate(self) -> None:
        """Grounded source description is preserved and not replaced by Atlas/LENS boilerplate."""
        grounded_desc = "Establish an encrypted relational database to retain quarterly payroll snapshots."
        proposals = [
            {
                "id": "proposal:payroll-db",
                "name": "Persistent Database For Payroll",
                "description": grounded_desc,
                "evidence_ids": ["ev-payroll-01"],
            }
        ]
        mutations = {"proposal_updates": proposals, "risk_updates": []}
        changes: list[str] = []

        heal_proposals_and_risks({}, mutations, changes)

        healed = mutations["proposal_updates"][0]
        self.assertEqual(healed["description"], grounded_desc)
        self.assertEqual(healed["evidence_ids"], ["ev-payroll-01"])
        self.assertNotIn("learning loop", healed["description"].lower())
        self.assertNotIn("atlas", healed["description"].lower())

    def test_non_atlas_ocr_proposal_preserves_wording_and_identity(self) -> None:
        """OCR proposal in general document is not rewritten to LENS Model Refinement Drivers."""
        ocr_desc = "Implement OCR extraction to ingest scanned PDF employee certification forms."
        proposals = [
            {
                "id": "proposal:ocr-cert-ingestion",
                "name": "OCR Certificate Ingestion",
                "description": ocr_desc,
                "evidence_ids": ["ev-cert-09"],
            }
        ]
        mutations = {"proposal_updates": proposals, "risk_updates": []}
        changes: list[str] = []

        heal_proposals_and_risks({}, mutations, changes)

        healed = mutations["proposal_updates"][0]
        self.assertEqual(healed["description"], ocr_desc)
        self.assertEqual(healed["evidence_ids"], ["ev-cert-09"])
        self.assertNotIn("lens", healed.get("name", "").lower())
        self.assertNotIn("model-refinement", healed.get("id", "").lower())

    def test_unsupported_relationship_injection_prevented(self) -> None:
        """Dangling edges to system:lens are not injected when system:lens is absent."""
        entities = [{"id": "system:continuous-learning-loop", "name": "Learning Loop"}]
        signals: list[dict] = []
        changes: list[str] = []

        _connect_atlas_and_golden(signals, entities, {}, changes)

        # No edge to system:lens should be created because system:lens does not exist
        lens_edges = [s for s in signals if "lens" in str(s.get("to_id", "")).lower()]
        self.assertEqual(len(lens_edges), 0)

        # Non-system golden entity should not inject proposal:golden-dataset-expansion
        other_entities = [{"id": "org:golden-gate-transit", "name": "Transit Agency"}]
        proposals: list[dict] = []
        mutations = {"proposal_updates": proposals, "risk_updates": [], "entity_upserts": other_entities}
        heal_proposals_and_risks({}, mutations, changes)
        self.assertEqual(len(mutations["proposal_updates"]), 0)

    def test_publication_failure_allows_zero_extraction_resume(self) -> None:
        """Downstream publication failure preserves validated synthesis for zero-extraction resume."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_resume_") as tmpdir:
            ckpt_path = Path(tmpdir) / "checkpoints" / "document_test.json"
            ckpt_path.parent.mkdir(parents=True, exist_ok=True)
            source_text = "HR360 test document content with enough text to validate."

            meta = {
                "is_document": True,
                "_document_checkpoint_path": str(ckpt_path),
                "run_id": "run-001",
                "project": "hr360",
                "filename_slug": "HR360",
                "date_filename": "10-08-2026",
            }

            fake_output_3 = json.dumps({
                "graph_mutations": {
                    "entity_upserts": [{"id": "org:hr360", "name": "HR360"}],
                    "relationship_signals": [],
                }
            })
            fake_synth, fake_eval = _build_valid_bundle(source_text, fake_output_3, attempt=1)
            synth_mock = MagicMock(return_value=(1, fake_synth, fake_eval))

            # Step 1: Initial run saves VALIDATED_SYNTHESIS
            from dual_pipeline.transcript_stages_core.offline_synthesizer import (
                OfflineTranscriptSynthesizer,
            )
            provider = OfflineTranscriptSynthesizer()
            res1 = run_synthesis_with_resume(
                synthesize=synth_mock,
                modality=CanonicalModality.DOCUMENT,
                source_text=source_text,
                metadata=meta,
                provider=provider,
                provider_type="offline",
                model_name="mock-model",
                reasoning_effort="high",
                pa_context={},
                max_judge_retries=0,
            )
            self.assertEqual(synth_mock.call_count, 1)

            # Step 2: Record downstream publication failure
            record_document_failure(meta, "publication", RuntimeError("Simulated DB lock during release"))

            # Step 3: Resumed run with identical input, model revision, code, and policy
            synth_mock.reset_mock()
            meta2 = dict(meta)
            meta2["run_id"] = "run-002"

            # Mock document evaluator and quality checker to accept cached candidate
            from unittest.mock import patch
            with patch("dual_pipeline.document_pipeline_core.evaluator.evaluate_document_qa_gates") as mock_qa, \
                 patch("dual_pipeline.document_pipeline_core.quality.qualify_document_judge", return_value=(None, True)):
                mock_qa.return_value = {"passed": True, "healed_artifacts": {"output_3": fake_output_3}}
                res2 = run_synthesis_with_resume(
                    synthesize=synth_mock,
                    modality=CanonicalModality.DOCUMENT,
                    source_text=source_text,
                    metadata=meta2,
                    provider=provider,
                    provider_type="offline",
                    model_name="mock-model",
                    reasoning_effort="high",
                    pa_context={},
                    max_judge_retries=0,
                )

            # Zero extraction calls on valid resume
            self.assertEqual(synth_mock.call_count, 0)
            self.assertTrue(meta2["document_resume"]["reused_synthesis"])
            self.assertEqual(meta2["document_resume"]["origin_run_id"], "run-001")

    def test_changed_source_input_invalidates_checkpoint_resume(self) -> None:
        """Changing document source text invalidates checkpoint binding and forces re-synthesis."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_bind_") as tmpdir:
            ckpt_path = Path(tmpdir) / "checkpoints" / "document_test.json"
            ckpt_path.parent.mkdir(parents=True, exist_ok=True)
            source_a = "Original source text version A."
            source_b = "Modified source text version B."

            meta = {
                "is_document": True,
                "_document_checkpoint_path": str(ckpt_path),
                "run_id": "run-001",
                "project": "hr360",
            }

            from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
            provider = OfflineTranscriptSynthesizer()

            fake_output_3 = json.dumps({"graph_mutations": {"entity_upserts": [{"id": "org:hr360"}]}})
            fake_synth_a, fake_eval_a = _build_valid_bundle(source_a, fake_output_3, attempt=1)
            fake_synth_b, fake_eval_b = _build_valid_bundle(source_b, fake_output_3, attempt=1)

            synth_mock = MagicMock(side_effect=[(1, fake_synth_a, fake_eval_a), (1, fake_synth_b, fake_eval_b)])

            # Initial run with source_a
            run_synthesis_with_resume(
                synthesize=synth_mock, modality=CanonicalModality.DOCUMENT,
                source_text=source_a, metadata=meta, provider=provider, provider_type="offline",
                model_name="mock", reasoning_effort="high", pa_context={}, max_judge_retries=0,
            )
            self.assertEqual(synth_mock.call_count, 1)

            # Run with source_b: binding mismatch forces new extraction
            meta2 = dict(meta)
            meta2["run_id"] = "run-002"
            run_synthesis_with_resume(
                synthesize=synth_mock, modality=CanonicalModality.DOCUMENT,
                source_text=source_b, metadata=meta2, provider=provider, provider_type="offline",
                model_name="mock", reasoning_effort="high", pa_context={}, max_judge_retries=0,
            )
            self.assertEqual(synth_mock.call_count, 2)
            self.assertFalse(meta2["document_resume"]["reused_synthesis"])

    def test_modified_healed_artifact_invalidates_checkpoint_resume(self) -> None:
        """Modifying the cached artifact so it disagrees with deterministic QA invalidates resume."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_artifact_") as tmpdir:
            ckpt_path = Path(tmpdir) / "checkpoints" / "document_test.json"
            ckpt_path.parent.mkdir(parents=True, exist_ok=True)
            source = "Original source text for artifact validation."

            meta = {
                "is_document": True,
                "_document_checkpoint_path": str(ckpt_path),
                "run_id": "run-001",
                "project": "hr360",
            }

            from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
            provider = OfflineTranscriptSynthesizer()

            fake_output_3 = json.dumps({"graph_mutations": {"entity_upserts": [{"id": "org:hr360"}]}})
            fake_synth, fake_eval = _build_valid_bundle(source, fake_output_3, attempt=1)
            synth_mock = MagicMock(return_value=(1, fake_synth, fake_eval))

            # Initial run saves VALIDATED_SYNTHESIS
            run_synthesis_with_resume(
                synthesize=synth_mock, modality=CanonicalModality.DOCUMENT,
                source_text=source, metadata=meta, provider=provider, provider_type="offline",
                model_name="mock", reasoning_effort="high", pa_context={}, max_judge_retries=0,
            )
            self.assertEqual(synth_mock.call_count, 1)

            # Rerun where deterministic QA produces a different healed artifact
            meta2 = dict(meta)
            meta2["run_id"] = "run-002"
            from unittest.mock import patch
            with patch("dual_pipeline.document_pipeline_core.evaluator.evaluate_document_qa_gates") as mock_qa, \
                 patch("dual_pipeline.document_pipeline_core.quality.qualify_document_judge", return_value=(None, True)):
                mock_qa.return_value = {"passed": True, "healed_artifacts": {"output_3": '{"healed": "different"}'}}
                with self.assertRaises(ValueError) as ctx:
                    from dual_pipeline.canonical_pipeline.document_resume import _validate_cached
                    payload = json.loads(ckpt_path.read_text())["stages"]["VALIDATED_SYNTHESIS"]
                    _validate_cached(payload, source, meta2)
                self.assertIn("CACHED_SYNTHESIS_INVALID", str(ctx.exception))

    def test_unresolved_model_revision_fails_closed_on_resume(self) -> None:
        """When resume is requested but model revision is unbound, execution fails closed."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_unbound_") as tmpdir:
            ckpt_path = Path(tmpdir) / "checkpoints" / "document_test.json"
            meta = {
                "is_document": True,
                "_document_checkpoint_path": str(ckpt_path),
                "resume": True,
                "project": "hr360",
                "extraction_model_revision": None,  # Unbound
            }

            from unittest.mock import MagicMock
            live_provider = MagicMock()
            live_provider.model = "gemini-live"

            with self.assertRaises(ValueError) as ctx:
                run_synthesis_with_resume(
                    synthesize=MagicMock(), modality=CanonicalModality.DOCUMENT,
                    source_text="Some document text", metadata=meta,
                    provider=live_provider, provider_type="gemini",
                    model_name="gemini-live", reasoning_effort="high",
                    pa_context={}, max_judge_retries=0,
                )
            self.assertIn("RESUME_FAILED_CLOSED", str(ctx.exception))
            self.assertIn("EXTRACTION_MODEL_REVISION_UNBOUND", str(ctx.exception))

    def test_repeated_identical_deterministic_failure_halts_circuit_breaker(self) -> None:
        """Repeated identical candidate digest halts loop via check_candidate_loop_termination."""
        from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult

        cand = SynthesisResult(output_1="T", output_2="R", output_3='{"entities": []}', attempt=1)
        _, digest1 = check_candidate_loop_termination(attempt=1, synthesis_result=cand, prior_digest=None)
        self.assertIsNotNone(digest1)

        # Attempt 2 with identical candidate digest triggers termination
        should_stop, digest2 = check_candidate_loop_termination(attempt=2, synthesis_result=cand, prior_digest=digest1)
        self.assertTrue(should_stop)
        self.assertEqual(digest1, digest2)


if __name__ == "__main__":
    unittest.main()
