"""Hermetic W3 lifecycle and bounded-repair acceptance cases."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.document_resume_cases import DocumentResumeCases, OfflineDocumentIsolation, _EMAIL


class DocumentW3Fixture(OfflineDocumentIsolation):
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
        from tests.document_execution_cases import DocumentExecutionCases
        if self._testMethodName in DocumentExecutionCases.__dict__:
            return
        self.original_synthesis = self.pipeline.provider.synthesize_graph
        self.synthesis_spy = self._start_patch(
            "dual_pipeline.transcript_stages_core.offline_synthesizer.OfflineTranscriptSynthesizer.synthesize_graph",
            side_effect=self.original_synthesis,
        )
        self.judge_spy = self._start_patch(
            "dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge.evaluate",
            autospec=True, side_effect=GraphExtractionJudge.evaluate,
        )

    _run_document = DocumentResumeCases._run_document
    _assert_complete = DocumentResumeCases._assert_complete

    def _emit(self, outcome, *, repair=0, calls=None, **details):
        observed = calls or {"extraction": self.synthesis_spy.call_count - repair,
                             "semantic_judge": self.judge_spy.call_count, "repair": repair,
                             "provider_attempts": 0}
        print(json.dumps({"wave3_case": {"test_id": self.id(),
              "measurement": "deterministic_provider_stub", "calls": observed,
              "tokens": {"input": None, "output": None, "reasoning": None},
              "terminal_outcome": outcome, **details}}, sort_keys=True))


    def _candidate_fixture(self):
        from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
        metadata = {**self.metadata, "is_document": True, "source_type": "document", "output3_only": True}
        valid = json.loads(DocumentGraphExtractorExecutor(self.pipeline.provider).execute(
            _EMAIL, pa_context={"metadata": metadata}))
        if not valid["evidence_index"]:
            quote = "We are not hiring junior software engineers at this point (full time or contract)."
            valid["evidence_index"]["ev-test"] = {
                "statement_id": "ev-test", "text": quote, "exact_text": quote,
                "locator": {"kind": "section", "value": "Document Body"},
            }
        identity = next(iter(valid["evidence_index"]))
        claims = [{"collection": "evidence_index", "id": identity, "reason": "Review exact source quotation"}]
        return valid, claims


class DocumentW3Cases(DocumentW3Fixture):
    def test_offline_effort_comparison_preserves_quality_without_changing_defaults(self):
        start = time.perf_counter()
        baseline = self._run_document()
        baseline_ms = (time.perf_counter() - start) * 1000
        baseline_graph = json.loads(Path(baseline["output_03"]).read_text())
        baseline_judge = json.loads(Path(baseline["llm_judge_evaluation"]).read_text())
        start = time.perf_counter()
        variant = self._run_document(extraction_effort="low", judge_effort="medium")
        variant_ms = (time.perf_counter() - start) * 1000
        variant_graph = json.loads(Path(variant["output_03"]).read_text())
        variant_judge = json.loads(Path(variant["llm_judge_evaluation"]).read_text())
        self.assertEqual(baseline_graph, variant_graph)
        self.assertEqual(baseline_judge["output_3_evaluation"]["composite_score"],
                         variant_judge["output_3_evaluation"]["composite_score"])
        self.assertEqual((baseline["status"], variant["status"]), ("COMPLETE", "COMPLETE"))
        self._emit("COMPLETE", calls={"extraction": 2, "semantic_judge": 2,
                   "repair": 0, "provider_attempts": 0}, comparison={
                   "scope": "offline_fixture_only", "default_ms": round(baseline_ms, 3),
                   "low_medium_ms": round(variant_ms, 3), "quality_equal": True})

    def test_clean_document_is_one_extraction_one_judge_with_final_receipt(self):
        result = self._run_document()
        metadata, _ = self._assert_complete(result)
        self.assertEqual((self.synthesis_spy.call_count, self.judge_spy.call_count), (1, 1))
        final = json.loads(Path(metadata["execution_evidence_path"]).read_text())
        self.assertEqual(final["terminal_outcome"], "COMPLETE")
        self.assertEqual(final["execution_timing"]["measurement_state"], "FINAL")
        self.assertIsNotNone(final["execution_timing"]["finished_at"])
        self.assertGreaterEqual(final["execution_timing"]["duration_ms"], 0)
        self._emit("COMPLETE")

    def test_explicit_efforts_reach_judge_and_offline_execution_receipts(self):
        result = self._run_document(extraction_effort="low", judge_effort="medium", fast_judge=True)
        metadata, _ = self._assert_complete(result)
        observed = metadata["_extraction_execution"]
        self.assertEqual(observed["requested_effort"], "low")
        self.assertEqual(observed["status"], "OFFLINE_FIXTURE")
        self.assertIsNone(observed["effective_effort"])
        self.assertEqual(self.judge_spy.call_args.args[0].reasoning_effort, "medium")
        report = json.loads(Path(result["llm_judge_evaluation"]).read_text())
        self.assertEqual(report["output_3_evaluation"]["execution"]["status"], "OFFLINE_FIXTURE")
        self._emit("COMPLETE", effective_settings={"extraction_requested": "low", "judge_requested": "medium"})


    def test_document_loop_caps_large_retry_request_at_one_repair(self):
        from dual_pipeline.canonical_pipeline.lifecycle import run_synthesis_loop
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
        from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
        valid, failed_claims = self._candidate_fixture()
        synthesis = MagicMock()
        synthesis.synthesize.side_effect = [SynthesisResult(output_3=json.dumps(valid), attempt=n) for n in (1, 2)]
        failed = SimpleNamespace(authoritative_write_authorized=False,
            final_decision={"status": "FAIL"}, qa_report={"passed": True},
            judge_report={"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL",
                          "failed_claims": failed_claims}})
        qa = MagicMock()
        qa.evaluate.return_value = failed
        with patch("dual_pipeline.canonical_pipeline.lifecycle.get_synthesis_strategy", return_value=synthesis), \
                patch("dual_pipeline.canonical_pipeline.lifecycle.get_qa_strategy", return_value=qa), \
                patch("dual_pipeline.canonical_pipeline.attempt_binding.BoundAttemptLedger") as ledger, \
                patch("dual_pipeline.remediation_core.state_machine.SelectiveRemediationStateMachine") as machine, \
                patch("dual_pipeline.canonical_pipeline.lifecycle._record_attempt_artifacts"), \
                patch("dual_pipeline.canonical_pipeline.lifecycle._extract_next_directive", return_value="Repair bound evidence"), \
                patch("dual_pipeline.transcript_pipeline_core.qa_runner._is_deterministic_only_failure", return_value=False):
            ledger.return_value.restore.side_effect = lambda attempt, result, evaluation, state: (result, evaluation)
            machine.return_value.get_prior_candidate.return_value = None
            attempt, _, evaluation = run_synthesis_loop(
                CanonicalModality.DOCUMENT, self.pipeline.provider, "offline", "fixture", "high",
                _EMAIL, {}, {}, self.root, 0, max_judge_retries=999,
            )
        self.assertEqual(attempt, 2)
        self.assertEqual((synthesis.synthesize.call_count, qa.evaluate.call_count), (2, 2))
        self.assertFalse(evaluation.authoritative_write_authorized)
        attempts = [call.kwargs["attempt"] for call in synthesis.synthesize.call_args_list]
        self.assertEqual(attempts, [1, 2])
        self._emit("FAIL", measurement="deterministic_strategy_stub", calls={
            "extraction": attempts.count(1), "repair": attempts.count(2),
            "semantic_judge": qa.evaluate.call_count, "provider_attempts": 0})

    def test_run_configuration_isolated_and_legacy_effort_only_controls_judge(self):
        from dual_pipeline.canonical_pipeline.run_configuration import prepare_run_configuration
        provider = SimpleNamespace(reasoning_effort="high", d03_model="declared-fixture")
        first, second = {}, {}
        config = prepare_run_configuration(provider, first, provider_type="llm", default_effort="high",
                                           extraction_effort="low", judge_effort="medium")
        legacy = prepare_run_configuration(provider, second, provider_type="llm", default_effort="high",
                                           legacy_judge_effort="low")
        self.assertIsNot(config.provider, provider)
        self.assertEqual((config.provider.reasoning_effort, config.judge_effort), ("low", "medium"))
        self.assertEqual((legacy.extraction_effort, legacy.judge_effort), ("high", "low"))
        self.assertEqual(provider.reasoning_effort, "high")
        self.assertIsNot(first["_extraction_execution"], second["_extraction_execution"])
        self.assertEqual(first["_extraction_execution"]["status"], "NOT_CALLED")
        self._emit("CONFIGURED_ONLY")

    def test_nested_deadline_and_backoff_cannot_extend_request_budget(self):
        from config.request_deadline import (
            RequestDeadlineExceeded, cooperative_sleep, deadline_scope, remaining_timeout,
        )
        clock = [100.0]
        waits = []
        def advance(seconds):
            waits.append(seconds)
            clock[0] += seconds
            return False
        with patch("config.request_deadline.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(RequestDeadlineExceeded):
                with deadline_scope(10):
                    self.assertEqual(remaining_timeout(90), 10)
                    clock[0] += 6
                    with deadline_scope(90) as child:
                        self.assertEqual(remaining_timeout(90), 4)
                        with patch.object(child.cancelled, "wait", side_effect=advance):
                            cooperative_sleep(5)
            self.assertEqual(remaining_timeout(90), 90)
        self.assertEqual(waits, [4])
        self._emit("DEADLINE_EXCEEDED", observed_backoff_seconds=waits[0])


    def test_expired_request_seals_failure_and_never_reaches_release(self):
        from config.request_deadline import RequestDeadlineExceeded
        clock = [100.0]
        def slow_fixture(*args, **kwargs):
            output = self.original_synthesis(*args, **kwargs)
            clock[0] += 10
            return output
        self.synthesis_spy.side_effect = slow_fixture
        with patch("config.request_deadline.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.pipeline, "_execute_evals_release") as release:
            with self.assertRaises(RequestDeadlineExceeded):
                self._run_document(timeout_seconds=5)
        release.assert_not_called()
        receipts = list((self.root / ".local/dual_pipeline/executions").glob("*/pipeline_execution_receipt.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["execution_timing"]["measurement_state"], "FINAL")
        self.assertIsNotNone(receipt["execution_timing"]["finished_at"])
        self._emit("FAILED", failure_type="RequestDeadlineExceeded")


    def test_invalid_deadlines_fail_without_entering_scope(self):
        from config.request_deadline import deadline_scope, remaining_timeout
        for value in (0, -1, True, float("nan"), float("inf"), "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                with deadline_scope(value):
                    self.fail("Invalid deadline entered a request scope")
        self.assertEqual(remaining_timeout(5), 5)
        self._emit("REJECTED")
