"""Offline W3 tests for effective settings, shared deadlines and reuse identity."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.document_resume_cases import OfflineDocumentIsolation, _EMAIL


class DocumentExecutionCases(OfflineDocumentIsolation):
    def setUp(self):
        super().setUp()
        from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
        from dual_pipeline.graph_scout_core.pipeline_hook import ScoutHookResult
        self.source = self.root / "email.txt"
        self.source.write_text(_EMAIL, encoding="utf-8")
        self.metadata = {"project": "nea", "title": "Mark McCarthy SWE Interviews and Pod Retentions",
                         "date": "09/17/2026", "author": "Mark McCarthy", "enforce_worktree": False,
                         "allow_duplicate": True, "dry_run_graph": True,
                         "graph_path": str(self.root / "absent-preview.sqlite")}
        for name in ("dual_pipeline.canonical_pipeline.pipeline.ROOT",
                     "dual_pipeline.canonical_pipeline.concurrency.ROOT"):
            self._start_patch(name, new=self.root)
        self._start_patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager")
        self._start_patch("config.credentials.CredentialManager.is_local_execution_mode", return_value=False)
        self._start_patch("dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook",
                          return_value=ScoutHookResult(skipped_reason="test isolation"))
        self.pipeline = DocumentPipeline(provider_type="offline")

    def _run_document(self, **options):
        return self.pipeline.run(self.source, metadata=self.metadata,
                                 output_dir=str(self.root / "output"), mutate_graph=False, **options)

    def _clock(self):
        clock = SimpleNamespace(value=0.0)
        clock.monotonic = lambda: clock.value
        self._start_patch("config.request_deadline.time", new=clock)
        return clock

    def _failed_receipt(self):
        receipts = list((self.root / ".local/dual_pipeline/executions").glob("*/pipeline_execution_receipt.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertTrue(receipt["evidence_digest"])
        self.assertIsNotNone(receipt["execution_timing"]["finished_at"])
        self.assertFalse((self.root / "absent-preview.sqlite").exists())
        return receipt

    def test_explicit_extraction_and_judge_effort_reach_real_offline_pipeline_and_receipts(self):
        from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge
        provider_class = type(self.pipeline.provider)
        original = provider_class.synthesize_graph
        used_efforts = []

        def synthesize(provider, *args, **kwargs):
            used_efforts.append(provider.reasoning_effort)
            return original(provider, *args, **kwargs)

        with patch.object(provider_class, "synthesize_graph", autospec=True, side_effect=synthesize) as extraction, \
                patch.object(GraphExtractionJudge, "evaluate", autospec=True,
                             side_effect=GraphExtractionJudge.evaluate) as judge:
            result = self._run_document(extraction_effort="low", judge_effort="medium", fast_judge=True)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(used_efforts, ["low"])
        self.assertEqual((extraction.call_count, judge.call_count), (1, 1))
        self.assertEqual(judge.call_args.args[0].reasoning_effort, "medium")
        self.assertFalse(hasattr(self.pipeline.provider, "reasoning_effort"))
        metadata = json.loads(Path(result["metadata"]).read_text())
        report = json.loads(Path(result["llm_judge_evaluation"]).read_text())
        self.assertEqual((metadata["extraction_effort"], metadata["judge_effort"]), ("low", "medium"))
        self.assertEqual(report["requested_reasoning_effort"], "medium")
        self.assertTrue(report["fast_judge"])
        self.assertEqual(report["extraction_execution"]["requested_effort"], "low")
        self.assertEqual(report["extraction_execution"]["status"], "OFFLINE_FIXTURE")
        self.assertIsNone(report["extraction_execution"]["effective_model"])

    def test_run_settings_preserve_defaults_and_legacy_judge_alias_without_mutating_provider(self):
        from dual_pipeline.canonical_pipeline.run_configuration import prepare_run_configuration
        provider = SimpleNamespace(reasoning_effort="high", d03_model="unchanged-model")
        base = prepare_run_configuration(provider, {}, provider_type="llm", default_effort="high")
        self.assertEqual((base.extraction_effort, base.judge_effort), ("high", "high"))
        changed = prepare_run_configuration(provider, {}, provider_type="llm", default_effort="high",
                                            extraction_effort="low", legacy_judge_effort="medium")
        self.assertEqual((changed.extraction_effort, changed.judge_effort), ("low", "medium"))
        self.assertEqual(changed.provider.d03_model, "unchanged-model")
        self.assertEqual(changed.provider.reasoning_effort, "low")
        self.assertEqual(provider.reasoning_effort, "high")
        self.assertIsNot(changed.provider, provider)
        with self.assertRaisesRegex(ValueError, "extraction_effort"):
            prepare_run_configuration(provider, {}, provider_type="llm", default_effort="high", extraction_effort="")

    def test_fast_judge_report_records_the_resolved_effort(self):
        from dual_pipeline.document_pipeline_core.evaluator import _execute_document_judge
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model
        model, _ = resolve_tier_model(ModelTier.FAST_ROUTING)
        result, _, _ = _execute_document_judge(model, "high", None, True, "{}", _EMAIL, True)
        self.assertEqual(result["execution"]["requested_effort"], "low")
        self.assertEqual(result["execution"]["effective_effort"], "low")

    def test_expired_provider_budget_prevents_judge_and_release_and_seals_failure(self):
        from config.request_deadline import RequestDeadlineExceeded
        clock = self._clock()
        provider_class = type(self.pipeline.provider)
        original = provider_class.synthesize_graph

        def exhaust(provider, *args, **kwargs):
            result = original(provider, *args, **kwargs)
            clock.value = 2.0
            return result

        with patch.object(provider_class, "synthesize_graph", autospec=True, side_effect=exhaust), \
                patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as judge, \
                patch.object(self.pipeline, "_execute_evals_release") as release:
            with self.assertRaises(RequestDeadlineExceeded):
                self._run_document(timeout_seconds=1)
        judge.assert_not_called()
        release.assert_not_called()
        self._failed_receipt()

    def test_deadline_exhausted_during_packaging_prevents_publication(self):
        from config.request_deadline import RequestDeadlineExceeded
        from dual_pipeline.canonical_pipeline import pipeline as canonical
        clock = self._clock()
        original = canonical.build_and_validate_intake_package

        def exhaust(*args, **kwargs):
            package = original(*args, **kwargs)
            clock.value = 2.0
            return package

        with patch.object(canonical, "build_and_validate_intake_package", side_effect=exhaust), \
                patch.object(canonical, "publish_graph_if_requested") as publish:
            with self.assertRaises((RequestDeadlineExceeded, RuntimeError)) as raised:
                self._run_document(timeout_seconds=1)
        self.assertIn("deadline", str(raised.exception).lower())
        publish.assert_not_called()
        self._failed_receipt()

    def test_transport_copies_parent_deadline_and_never_resets_it_in_worker(self):
        from config.request_deadline import RequestDeadlineExceeded, deadline_scope, remaining_timeout
        from config.transport import execute_with_deadline_and_heartbeat
        clock = self._clock()
        observed = []

        def worker():
            observed.append(remaining_timeout(100))
            clock.value = 6.0
            remaining_timeout(100)

        with self.assertRaises(RequestDeadlineExceeded), deadline_scope(5):
            execute_with_deadline_and_heartbeat(worker, deadline_seconds=100)
        self.assertEqual(observed, [5.0])

    def test_provider_backoff_cannot_buy_a_fresh_deadline(self):
        from config.request_deadline import RequestDeadlineExceeded, check_deadline, deadline_scope
        from dual_pipeline.transcript_stages_core.completion_call import create_completion_with_effort
        clock = self._clock()
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            clock.value = 4.0
            raise RuntimeError("503 temporary transport failure")

        def backoff(seconds):
            clock.value += seconds
            check_deadline()

        provider = SimpleNamespace(model="gpt-6.1-sol", _document_execution_receipt={},
                                   client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        with patch("dual_pipeline.otel_engine.trace_llm_execution", return_value=MagicMock()), \
                patch("dual_pipeline.transcript_stages_core.completion_call.cooperative_sleep", side_effect=backoff), \
                self.assertRaises(RequestDeadlineExceeded), deadline_scope(5):
            create_completion_with_effort(provider, "low", "L2-Stage03 Graph Extraction", 100,
                                          model=provider.model, messages=[])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], 5.0)

    def test_judge_does_not_fall_back_after_deadline_expires(self):
        from config.request_deadline import RequestDeadlineExceeded, deadline_scope
        from dual_pipeline.llm_judge_core.judges.graph_transport import generate_client_graph
        clock = self._clock()

        def create(**kwargs):
            clock.value = 6.0
            raise RuntimeError("429 rate limit")

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content") as fallback, \
                self.assertRaises(RequestDeadlineExceeded), deadline_scope(5):
            generate_client_graph(client, "gpt-6.1-sol", "low", "system", "source", MagicMock(),
                                  100, lambda value: value, lambda exc: True)
        fallback.assert_not_called()

    def test_observed_model_effort_and_revision_mismatches_prevent_reuse(self):
        from dual_pipeline.canonical_pipeline.execution_identity import execution_reuse_blockers
        binding = {"components": {
            "extraction": {"declared_model": "extract", "declared_revision": "r1",
                           "settings": {"reasoning_effort": "low"}},
            "judge": {"declared_model": "judge", "declared_revision": "r2", "reasoning_effort": "medium"},
        }}
        evaluation = SimpleNamespace(judge_report={
            "extraction_execution": {"status": "COMPLETED", "effective_model": "extract",
                                     "effective_effort": "low", "observed_revision": "r1"},
            "execution": {"status": "COMPLETED", "effective_model": "judge",
                          "effective_effort": "medium", "observed_revision": "r2"},
        })
        self.assertEqual(execution_reuse_blockers(binding, evaluation), [])
        for key, value, expected in (("effective_model", "substitute", "MODEL"),
                                     ("effective_effort", "high", "EFFORT"),
                                     ("observed_revision", "changed", "REVISION")):
            candidate = deepcopy(evaluation)
            candidate.judge_report["extraction_execution"][key] = value
            with self.subTest(field=key):
                self.assertIn("EXTRACTION_OBSERVED_" + expected + "_MISMATCH",
                              execution_reuse_blockers(binding, candidate))
