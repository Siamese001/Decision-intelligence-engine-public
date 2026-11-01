"""Hermetic reusable cases for exact document synthesis checkpoints."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

_EMAIL = (
    "That's correct, Thad will not be going through the formal screen ahead of Q4'26. "
    "We also interviewed four others and we would like to retain Chris Chockley if available. "
    "We are not hiring junior software engineers at this point (full time or contract). "
    "Please align with engineering leadership on the final pod staffing roster changes by end of week."
)


class OfflineDocumentIsolation:
    """Keep telemetry and human decision ledgers inside disposable test state."""
    def setUp(self):
        from opentelemetry.trace import NoOpTracerProvider
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, kwargs in (
            ("dual_pipeline.otel_engine.get_tracer", {"return_value": NoOpTracerProvider().get_tracer("test")}),
            ("dual_pipeline.telemetry_core.metrics.record_stage_duration", {}),
            ("dual_pipeline.hitl_governance_core.ledger._find_default_ledger_path",
             {"return_value": self.root / "hitl.json"}),
        ):
            self._start_patch(target, **kwargs)

    def _start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result


class _DeclaredProvider:
    """Identity-only stub: test binding construction must never invoke synthesis."""
    d03_model = "gpt-6.1-sol"
    reasoning_effort = "high"
    use_complexity_classifier = False

    def synthesize_graph(self, *args, **kwargs):
        raise AssertionError("Binding construction must never call a provider")


class DocumentResumeCases(OfflineDocumentIsolation):
    """Inherited by the enrolled end-to-end unittest class; no paid calls."""
    def setUp(self):
        super().setUp()
        from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
        from dual_pipeline.graph_scout_core.pipeline_hook import ScoutHookResult
        from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge
        self.source = self.root / "email.txt"
        self.source.write_text(_EMAIL, encoding="utf-8")
        self.metadata = {
            "project": "nea", "title": "Mark McCarthy SWE Interviews and Pod Retentions",
            "date": "09/17/2026", "author": "Mark McCarthy", "enforce_worktree": False,
            "allow_duplicate": True, "dry_run_graph": True,
            "graph_path": str(self.root / "absent-preview.sqlite"),
        }
        for target in ("dual_pipeline.canonical_pipeline.pipeline.ROOT",
                       "dual_pipeline.canonical_pipeline.concurrency.ROOT"):
            self._start_patch(target, new=self.root)
        self._start_patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager")
        self._start_patch("dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook",
                          return_value=ScoutHookResult(skipped_reason="test isolation"))
        self.pipeline = DocumentPipeline(provider_type="offline")
        spy = patch.object(self.pipeline.provider, "synthesize_graph", wraps=self.pipeline.provider.synthesize_graph)
        self.synthesis_spy = spy.start()
        self.addCleanup(spy.stop)
        self.judge_spy = self._start_patch(
            "dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge.evaluate",
            autospec=True, side_effect=GraphExtractionJudge.evaluate,
        )

    def _run_document(self, *, metadata=None, **options):
        return self.pipeline.run(
            self.source, metadata={**self.metadata, **(metadata or {})},
            output_dir=str(self.root / "output"), mutate_graph=False, **options,
        )

    def _fail_packaging(self):
        with patch("dual_pipeline.canonical_pipeline.pipeline.build_and_validate_intake_package",
                   side_effect=RuntimeError("synthetic packaging interruption")) as packaging:
            with self.assertRaisesRegex(RuntimeError, "synthetic packaging interruption"):
                self._run_document()
        packaging.assert_called_once()
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (1, 1))
        files = list((self.root / ".local/dual_pipeline/checkpoints").glob("document_*.json"))
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text())
        self.assertIn("VALIDATED_SYNTHESIS", saved["stages"])
        self.assertEqual(saved["stages"]["LAST_FAILURE"]["stage"], "EVALS_RELEASE")
        return files[0], saved

    def _assert_complete(self, result):
        from dual_pipeline.canonical_pipeline.attempt_binding import verify_attempt_binding
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
        from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
        from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
        from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(Path(result["intake_package"]).is_file())
        receipt = json.loads(Path(result["execution_receipt"]).read_text())
        self.assertEqual(receipt["terminal_outcome"], "COMPLETE")
        self.assertEqual(set(receipt["stages"]), {stage.value for stage in PIPELINE_STAGE_SEQUENCE})
        self.assertTrue(all(stage["status"] == "PASS" for stage in receipt["stages"].values()))
        qa, judge, decision = (json.loads(Path(result[key]).read_text()) for key in
                               ("qa_gates_report", "llm_judge_evaluation", "final_release_decision"))
        graph = Path(result["output_03"]).read_text()
        verify_attempt_binding(SynthesisResult(output_3=graph),
                               QAEvaluationResult(True, qa, judge, decision, True),
                               self.source.read_text(), CanonicalModality.DOCUMENT)
        self.assertFalse((self.root / "absent-preview.sqlite").exists())
        metadata = json.loads((Path(result["output_03"]).parent / "metadata.json").read_text())
        return metadata, receipt

    def test_package_failure_reuses_exact_synthesis_and_judge_but_reruns_current_stages(self):
        checkpoint, before = self._fail_packaging()
        with patch.object(self.pipeline, "_execute_u0", wraps=self.pipeline._execute_u0) as ingress, \
                patch.object(self.pipeline, "_execute_c0_pa", wraps=self.pipeline._execute_c0_pa) as context:
            result = self._run_document(metadata={"resume": True, "request_id": "fresh-request"})
        ingress.assert_called_once()
        context.assert_called_once()
        metadata, receipt = self._assert_complete(result)
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (1, 1),
                         metadata["document_resume"])
        self.assertTrue(metadata["document_resume"]["reused_synthesis"])
        after = json.loads(checkpoint.read_text())
        self.assertEqual(before["binding_digest"], after["binding_digest"])
        self.assertEqual(before["stages"]["VALIDATED_SYNTHESIS"], after["stages"]["VALIDATED_SYNTHESIS"])
        final_receipt = json.loads(Path(metadata["execution_evidence_path"]).read_text())
        self.assertTrue(final_receipt["evidence_digest"])

    def test_force_invalidates_a_valid_checkpoint_and_generates_new_evidence(self):
        checkpoint, _ = self._fail_packaging()
        result = self._run_document(metadata={"resume": True, "force_rerun": True})
        metadata, _ = self._assert_complete(result)
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (2, 2))
        self.assertFalse(metadata["document_resume"]["reused_synthesis"])
        self.assertEqual(metadata["document_resume"]["reason"], "FORCE_INVALIDATION")
        events = json.loads(checkpoint.read_text())["invalidations"]
        self.assertIn("FORCE_INVALIDATION", [item["reason"] for item in events])

    def test_current_source_bytes_are_reloaded_and_cannot_reuse_old_synthesis(self):
        self._fail_packaging()
        self.source.write_text(_EMAIL + "\n", encoding="utf-8")
        result = self._run_document(metadata={"resume": True})
        metadata, _ = self._assert_complete(result)
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (2, 2))
        self.assertFalse(metadata["document_resume"]["reused_synthesis"])
        self.assertEqual(metadata["raw_source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_execution_options_change_invalidates_synthesis(self):
        checkpoint, before = self._fail_packaging()
        result = self._run_document(max_judge_retries=1)
        metadata, _ = self._assert_complete(result)
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (2, 2))
        self.assertFalse(metadata["document_resume"]["reused_synthesis"])
        self.assertEqual(metadata["document_resume"]["reason"], "BINDING_CHANGED")
        self.assertNotEqual(before["binding_digest"], json.loads(checkpoint.read_text())["binding_digest"])

    def test_corrupt_checkpoint_never_silently_retries_provider_even_when_forced(self):
        from dual_pipeline.checkpoint_storage import CheckpointIntegrityError
        checkpoint, saved = self._fail_packaging()
        saved["stages"]["VALIDATED_SYNTHESIS"]["synthesis"]["output_3"] = "{}"
        checkpoint.write_text(json.dumps(saved), encoding="utf-8")
        for forced in (False, True):
            with self.subTest(forced=forced), self.assertRaises(CheckpointIntegrityError) as raised:
                self._run_document(metadata={"resume": True, "force_rerun": forced})
            self.assertEqual(raised.exception.reason, "CORRUPT_CHECKPOINT")
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (1, 1))

    def _build_binding(self, *, metadata=None, **overrides):
        from dual_pipeline.canonical_pipeline.document_resume_binding import build_document_resume_binding
        meta = {**self.metadata, **(metadata or {})}
        arguments = dict(source_text=self.source.read_text(), source_path=self.source,
                         metadata=meta, provider=self.pipeline.provider, provider_type="offline",
                         model_name=self.pipeline.model_name, reasoning_effort=self.pipeline.reasoning_effort,
                         context={"metadata": meta})
        arguments.update(overrides)
        with patch("config.credentials.CredentialManager.is_local_execution_mode", return_value=False):
            return build_document_resume_binding(**arguments)

    def test_binding_and_actual_prompt_ignore_only_operational_metadata(self):
        from dual_pipeline.document_pipeline_core.executor import _build_document_user_content
        baseline = self._build_binding()
        operations = {"request_id": "second", "run_id": "run-two", "resume": True, "force_rerun": True,
                      "output_dir": "/unused/staging", "generation_id": "new-generation",
                      "state_machine": object(), "_document_checkpoint": object(),
                      "document_resume": {"reused_synthesis": True}}
        changed = {**self.metadata, **operations}
        self.assertTrue(baseline["reusable"])
        self.assertEqual(baseline, self._build_binding(metadata=operations))
        self.assertEqual(_build_document_user_content(_EMAIL, self.metadata),
                         _build_document_user_content(_EMAIL, changed))
        self.synthesis_spy.assert_not_called()
        self.judge_spy.assert_not_called()

    def test_semantic_metadata_and_graph_context_change_the_binding(self):
        baseline = self._build_binding()["binding_sha256"]
        changes = {"author": "Another Author", "title": "Changed scope", "date": "09/18/2026",
                   "audiences": ["reviewers"], "classification": "restricted",
                   "hitl_ids": ["authenticated-decision-2"], "prior_context": "Updated graph fact",
                   "user_clarifications": "Approved correction"}
        for key, value in changes.items():
            with self.subTest(key=key):
                self.assertNotEqual(baseline, self._build_binding(metadata={key: value})["binding_sha256"])
        self.assertNotEqual(baseline, self._build_binding(
            context={"metadata": self.metadata, "graph_facts": [{"status": "changed"}]})["binding_sha256"])

    def test_binding_rehashes_raw_bytes_and_rejects_stale_extraction_receipt(self):
        baseline = self._build_binding()
        raw_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        moved = self.root / "same-bytes.txt"
        moved.write_bytes(self.source.read_bytes())
        self.assertEqual(baseline, self._build_binding(source_path=moved))
        self.source.write_text(_EMAIL + "\n", encoding="utf-8")
        current = self._build_binding(source_text=_EMAIL)
        self.assertNotEqual(baseline["components"]["source"]["raw_sha256"],
                            current["components"]["source"]["raw_sha256"])
        with self.assertRaisesRegex(ValueError, "Source changed after extraction"):
            self._build_binding(metadata={"raw_source_sha256": raw_hash}, source_text=_EMAIL)
        with self.assertRaisesRegex(ValueError, "disagree"):
            self._build_binding(raw_source_bytes=b"different")

    def test_live_model_revisions_must_be_declared_and_actual_provider_model_is_bound(self):
        provider = _DeclaredProvider()
        unbound = self._build_binding(provider=provider, provider_type="llm")
        self.assertFalse(unbound["reusable"])
        self.assertIn("EXTRACTION_MODEL_REVISION_UNBOUND", unbound["reuse_blockers"])
        self.assertIn("JUDGE_MODEL_REVISION_UNBOUND", unbound["reuse_blockers"])
        revisions = {"extraction_model_revision": "trusted-provider-revision-1",
                     "judge_model_revision": "trusted-judge-revision-1"}
        bound = self._build_binding(provider=provider, provider_type="llm", metadata=revisions)
        self.assertTrue(bound["reusable"])
        self.assertEqual(bound["components"]["extraction"]["declared_model"], provider.d03_model)
        provider.d03_model = "gpt-6-astra"
        changed = self._build_binding(provider=provider, provider_type="llm", metadata=revisions)
        self.assertNotEqual(bound["binding_sha256"], changed["binding_sha256"])
        provider.d03_model = None
        self.assertIn("EXTRACTION_MODEL_UNBOUND", self._build_binding(
            provider=provider, provider_type="llm", metadata=revisions)["reuse_blockers"])

    def test_binding_rejects_nonfinite_semantic_metadata(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            self._build_binding(metadata={"confidence": float("nan")})
