"""Hermetic document ingestion, quality, artifact binding and resume regressions."""
from __future__ import annotations

from tests.document_w3_cases import DocumentW3Cases
from tests.document_execution_cases import DocumentExecutionCases

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

from tests.document_resume_cases import DocumentResumeCases, OfflineDocumentIsolation

from dual_pipeline.canonical_pipeline.attempt_binding import BoundAttemptLedger, verify_attempt_binding
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_judge_and_release
from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.evidence_field_limits import (
    envelope_field_limits, source_text_statements, validate_projection_field_lengths,
    validate_source_span_locator,
)
from dual_pipeline.llm_judge_core.judges.graph import DIMENSION_KEYS
from dual_pipeline.release_policy import build_artifact_manifest, compute_release_evaluation_digest

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SOURCE = "Synthetic source retained exactly for artifact binding."


def _scorecard(*, verdict="PASS", grounding=18):
    values = [15, 15, 20, 14, 15, grounding]
    return dict(zip(DIMENSION_KEYS, values), composite_score=sum(values), verdict=verdict)


def _docx_bytes():
    document = ET.Element(f"{_W}document")
    body = ET.SubElement(document, f"{_W}body")

    def paragraph(parent, text, heading=False):
        para = ET.SubElement(parent, f"{_W}p")
        if heading:
            props = ET.SubElement(para, f"{_W}pPr")
            ET.SubElement(props, f"{_W}pStyle", {f"{_W}val": "Heading1"})
        ET.SubElement(ET.SubElement(para, f"{_W}r"), f"{_W}t").text = text

    paragraph(body, "Synthetic scope heading", heading=True)
    table = ET.SubElement(body, f"{_W}tbl")
    for row in range(31):
        tr = ET.SubElement(table, f"{_W}tr")
        for column in range(5):
            tc = ET.SubElement(tr, f"{_W}tc")
            text = f"Header {column + 1}" if row == 0 else f"Item {row:02d} field {column + 1}"
            paragraph(tc, text)
    paragraph(body, "Synthetic follow-up heading", heading=True)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", ET.tostring(document, encoding="utf-8"))
    return data.getvalue()


def _bundle(text, scores=(100, 100, 88), *, attempt=1, modality=CanonicalModality.DOCUMENT):
    outputs = (None, None, text) if modality == CanonicalModality.DOCUMENT else (text + "-1", text + "-2", text)
    synthesis = SynthesisResult(*outputs, attempt=attempt)
    judge = {f"output_{n}_evaluation": {"composite_score": score, "verdict": "FAIL",
                                      "forensic_rationale": f"attempt-{attempt}-output-{n}"}
             for n, score in enumerate(scores, start=1)}
    judge["status"] = "FAIL"
    judge["evaluation_receipt_digest"] = compute_release_evaluation_digest(judge)
    qa = {"passed": False, "failures": [f"attempt-{attempt}"],
          "healed_artifacts": {f"output_{n}": value or "" for n, value in enumerate(outputs, start=1)}}
    decision = {"status": "FAIL", "attempt": attempt, "authoritative_write_authorized": False,
                "cross_artifact_consistency_pass": False}
    for number in range(1, 4):
        for suffix in ("pass", "deterministic_gates_pass", "llm_score_pass"):
            decision[f"output_{number}_{suffix}"] = False
    decision["artifact_manifest"] = build_artifact_manifest(
        _SOURCE if modality == CanonicalModality.DOCUMENT else outputs[0], outputs[1] or "", text,
        "fixture", "none", judge["evaluation_receipt_digest"],
    )
    return synthesis, QAEvaluationResult(False, qa, judge, decision, False)


class Wave1LifecycleTests(OfflineDocumentIsolation, unittest.TestCase):
    def test_docx_preserves_all_30_entries_31_rows_155_cells_and_headings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic_scope.docx"
            raw = _docx_bytes()
            source.write_bytes(raw)
            metadata = {}
            text, source_hash, fmt = load_document_content(source, metadata=metadata)
        self.assertEqual(fmt, "docx")
        self.assertEqual(source_hash, hashlib.sha256(raw).hexdigest())
        coverage = metadata["_source_coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual((coverage["table_count"], coverage["row_count"], coverage["cell_count"]), (1, 31, 155))
        self.assertEqual(coverage["expected_text_nodes"], coverage["preserved_text_nodes"])
        self.assertEqual(len(coverage["cells"]), 155)
        self.assertNotIn("cells", metadata["source_coverage"])
        self.assertIn("# Synthetic scope heading", text)
        self.assertIn("# Synthetic follow-up heading", text)
        self.assertLess(text.index("Synthetic scope heading"), text.index("[T1.R1.C1]"))
        self.assertLess(text.index("[T1.R31.C5]"), text.index("Synthetic follow-up heading"))
        for row in range(1, 31):
            for column in range(1, 6):
                self.assertIn(f"[T1.R{row + 1}.C{column}] Item {row:02d} field {column}", text)

    def test_schema_field_boundaries_accept_exact_limit_and_never_clip(self):
        limits = envelope_field_limits()
        payload = {"meeting": {"title": "t" * limits["title"]},
                   "speakers": [{"statements": [{"text": "s" * limits["statement"]}]}],
                   "actions": [{"action": "a" * limits["action"]}]}
        original = deepcopy(payload)
        validate_projection_field_lengths(payload)
        self.assertEqual(payload, original)
        for kind in ("statement", "action", "title"):
            bad = deepcopy(payload)
            if kind == "statement":
                bad["speakers"][0]["statements"][0]["text"] += "!"
            elif kind == "action":
                bad["actions"][0]["action"] += "!"
            else:
                bad["meeting"]["title"] += "!"
            unchanged = deepcopy(bad)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "silent truncation"):
                validate_projection_field_lengths(bad)
            self.assertEqual(bad, unchanged)

    def test_oversized_generated_claim_rejected_before_healer_or_judge(self):
        payload = {"evidence_index": {"e1": {"text": "x" * (envelope_field_limits()["statement"] + 1)}}}
        with patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as judge, \
                patch("dual_pipeline.transcript_qa_core.healing_mesh.DeterministicHealer.heal_output_3") as healer:
            with self.assertRaisesRegex(ValueError, "evidence_index"):
                evaluate_document_judge_and_release(payload, _SOURCE, mock=True)
        judge.assert_not_called()
        healer.assert_not_called()

    def test_7099_character_source_chunks_are_lossless_and_locator_bound(self):
        text = ("αβ\nraw-source-🙂|" * 600)[:7099]
        self.assertEqual(len(text), 7099)
        statements = source_text_statements(text)
        self.assertEqual([len(item["text"]) for item in statements], [4000, 3099])
        self.assertEqual("".join(item["text"] for item in statements), text)
        for item in statements:
            self.assertTrue(validate_source_span_locator(item, item["locator"], text))
        tampered = deepcopy(statements[-1])
        tampered["text"] = tampered["text"][:-1] + "!"
        with self.assertRaisesRegex(ValueError, "exact evidence"):
            validate_source_span_locator(tampered, tampered["locator"], text)

    def test_quality_requires_dimensions_and_raw_pass_not_just_composite(self):
        good, passed, score = qualify_document_judge(_scorecard())
        self.assertTrue(passed)
        self.assertEqual(score, 97)
        floor_failure = _scorecard(grounding=12)
        self.assertGreaterEqual(floor_failure["composite_score"], 90)
        for candidate in (floor_failure, _scorecard(verdict="FAIL"), _scorecard(verdict="HITL_REQUIRED")):
            with self.subTest(candidate=candidate):
                result, passed, _ = qualify_document_judge(candidate)
                self.assertFalse(passed)
                self.assertNotEqual(result["verdict"], "PASS")
        self.assertEqual(good["verdict"], "PASS")

    def test_malformed_and_inconsistent_scores_fail_closed(self):
        malformed = [None, True, "15", float("nan"), float("inf"), -1, 16, 14.5]
        for value in malformed:
            candidate = _scorecard()
            candidate[DIMENSION_KEYS[0]] = value
            with self.subTest(value=value):
                result, passed, score = qualify_document_judge(candidate)
                self.assertFalse(passed)
                self.assertEqual((result["verdict"], score), ("FAIL", 0))
        for value in (None, True, "97", float("nan"), float("inf"), 101, 96):
            candidate = _scorecard()
            candidate["composite_score"] = value
            with self.subTest(composite=value):
                self.assertFalse(qualify_document_judge(candidate)[1])
        candidate = _scorecard()
        candidate.pop(DIMENSION_KEYS[-1])
        self.assertFalse(qualify_document_judge(candidate)[1])

    def test_successful_evaluator_seals_exact_post_qa_artifact(self):
        raw, healed = "{}", '{"healed":true}'
        qa = {"passed": True, "status": "PASS", "healed_artifacts": {"output_3": healed},
              "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"}}
        with patch("dual_pipeline.transcript_qa_core.healing_mesh.DeterministicHealer.heal_output_3",
                   return_value=(raw, [])), \
                patch("dual_pipeline.document_pipeline_core.evaluator.evaluate_document_qa_gates", return_value=qa), \
                patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as judge:
            judge.return_value.evaluate.return_value = _scorecard()
            qa_report, judge_report, decision = evaluate_document_judge_and_release(raw, _SOURCE, mock=True)
        self.assertTrue(decision["authoritative_write_authorized"])
        self.assertEqual(judge.return_value.evaluate.call_args.kwargs["f3_text"], healed)
        evaluation = QAEvaluationResult(True, qa_report, judge_report, decision, True)
        verify_attempt_binding(SynthesisResult(output_3=healed), evaluation, _SOURCE, CanonicalModality.DOCUMENT)
        with self.assertRaisesRegex(ValueError, "binding failed"):
            verify_attempt_binding(SynthesisResult(output_3=raw), evaluation, _SOURCE, CanonicalModality.DOCUMENT)

    def test_rollback_restores_complete_matching_report_and_candidate_snapshot(self):
        prior_s, prior_e = _bundle('{"candidate":"prior"}', attempt=1)
        current_s, current_e = _bundle('{"candidate":"current"}', scores=(100, 100, 80), attempt=2)
        ledger = BoundAttemptLedger(_SOURCE, CanonicalModality.DOCUMENT)
        ledger.record(1, prior_s, prior_e)
        ledger.record(2, current_s, current_e)
        original_report = deepcopy(prior_e.judge_report)
        original_qa = deepcopy(prior_e.qa_report)
        original_manifest = deepcopy(prior_e.final_decision["artifact_manifest"])
        prior_e.qa_report["failures"].append("external mutation")
        prior_s.output_3 = "mutated after record"
        state = MagicMock()
        state.has_regressed.return_value = True
        restored_s, restored_e = ledger.restore(2, current_s, current_e, state)
        self.assertEqual(restored_s.output_3, '{"candidate":"prior"}')
        self.assertEqual(restored_e.judge_report, original_report)
        self.assertEqual(restored_e.qa_report, original_qa)
        self.assertEqual(restored_e.final_decision["artifact_manifest"], original_manifest)
        self.assertFalse(restored_e.authoritative_write_authorized)
        verify_attempt_binding(restored_s, restored_e, _SOURCE, CanonicalModality.DOCUMENT)

    def test_mixed_winners_cannot_restore_an_unjudged_combination(self):
        modality = CanonicalModality.MEETING_TRANSCRIPT
        prior_s, prior_e = _bundle("prior", scores=(95, 80, 90), modality=modality)
        current_s, current_e = _bundle("current", scores=(85, 92, 90), attempt=2, modality=modality)
        ledger = BoundAttemptLedger(_SOURCE, modality)
        ledger.record(1, prior_s, prior_e)
        ledger.record(2, current_s, current_e)
        state = MagicMock()
        state.has_regressed.return_value = True
        selected_s, selected_e = ledger.restore(2, current_s, current_e, state)
        self.assertIs(selected_s, current_s)
        self.assertIs(selected_e, current_e)
        self.assertFalse(selected_e.authoritative_write_authorized)
        state.freeze_artifact.assert_not_called()

    def test_tampered_report_or_different_source_cannot_enter_attempt_ledger(self):
        for mutation in ("report", "source", "artifact"):
            synthesis, evaluation = _bundle("candidate")
            source = _SOURCE
            if mutation == "report":
                evaluation.judge_report["output_3_evaluation"]["composite_score"] = 99
            elif mutation == "source":
                source += "!"
            else:
                synthesis.output_3 += "!"
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "binding failed"):
                BoundAttemptLedger(source, CanonicalModality.DOCUMENT).record(1, synthesis, evaluation)

    def test_raw_provider_fail_or_inconsistent_score_cannot_be_normalized_to_pass(self):
        from dual_pipeline.llm_judge_core.judges.graph import _assign_graph_verdict
        failed = _scorecard(verdict="FAIL")
        failed["raw_judge_scorecard"] = {"verdict": "PASS"}  # Spoofed provenance is replaced.
        inconsistent = _scorecard()
        inconsistent["composite_score"] = 96  # Dimensions sum to 97.
        fractional = _scorecard()
        fractional[DIMENSION_KEYS[0]] = 14.5
        for raw in (failed, inconsistent, fractional):
            with self.subTest(raw=raw):
                assigned = _assign_graph_verdict(deepcopy(raw))
                result, passed, _ = qualify_document_judge(assigned)
                self.assertFalse(passed)
                self.assertEqual(result["verdict"], "FAIL")
                self.assertEqual(assigned["raw_judge_scorecard"]["composite_score"], raw["composite_score"])
        self.assertEqual(_assign_graph_verdict(failed)["raw_judge_scorecard"]["verdict"], "FAIL")

    def test_raw_verdict_absence_and_borderline_floor_keep_existing_semantics(self):
        from dual_pipeline.llm_judge_core.judges.graph import _assign_graph_verdict
        raw = _scorecard()
        raw.pop("verdict")
        self.assertTrue(qualify_document_judge(_assign_graph_verdict(raw))[1])
        borderline = _assign_graph_verdict(_scorecard(grounding=13))
        self.assertEqual(borderline["verdict"], "HITL_REQUIRED")
        result, passed, score = qualify_document_judge(borderline)
        self.assertFalse(passed)
        self.assertEqual(result["verdict"], "HITL_REQUIRED")
        self.assertGreaterEqual(score, 90)

    def test_notes_binding_uses_exact_blank_or_healed_output_one(self):
        modality = CanonicalModality.MEETING_NOTES
        for first_output in ("", "# Synthesized notes metadata header"):
            with self.subTest(first_output=first_output):
                synthesis, evaluation = _bundle("notes graph", modality=modality)
                synthesis.output_1 = first_output or None
                evaluation.qa_report["healed_artifacts"]["output_1"] = first_output
                evaluation.final_decision["artifact_manifest"] = build_artifact_manifest(
                    first_output, synthesis.output_2, synthesis.output_3,
                    "fixture", "none", evaluation.judge_report["evaluation_receipt_digest"],
                )
                verify_attempt_binding(synthesis, evaluation, _SOURCE, modality)
                synthesis.output_1 = "different output"
                with self.assertRaisesRegex(ValueError, "binding failed"):
                    verify_attempt_binding(synthesis, evaluation, _SOURCE, modality)

    def test_transcript_short_circuit_seals_tier_metadata_before_binding(self):
        from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
        failed_qa = {"passed": False, "output_03_canonical_signal_projection":
                     {"passed": False, "failures": ["Intentional deterministic failure"]}}
        with patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates", return_value=failed_qa), \
                patch("dual_pipeline.transcript_qa_core.evaluator._resolve_judge_tier_and_model",
                      return_value=("fast", "fixture", "none")), \
                patch("dual_pipeline.transcript_qa_core.evaluator.DecomposedMultiJudge") as judge:
            qa, report, decision = evaluate_judge_and_release("f1", "f2", "{}", mock=False)
        judge.assert_not_called()
        self.assertEqual(report["judge_tier"], "fast")
        self.assertEqual(report["judge_model"], "fixture")
        self.assertFalse(decision["authoritative_write_authorized"])
        evaluation = QAEvaluationResult(False, qa, report, decision, False)
        verify_attempt_binding(SynthesisResult("f1", "f2", "{}"), evaluation,
                               _SOURCE, CanonicalModality.MEETING_TRANSCRIPT)

    def test_offline_email_completes_real_extraction_qa_packaging_and_promotion(self):
        from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
        from dual_pipeline.graph_scout_core.pipeline_hook import ScoutHookResult
        from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE
        email = ("That's correct, Thad will not be going through the formal screen ahead of Q4'26. "
                 "We also interviewed four others and we would like to retain Chris Chockley if available. "
                 "We are not hiring junior software engineers at this point (full time or contract). "
                 "Please align with engineering leadership on the final pod staffing roster changes by end of week.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "email.txt"
            source.write_text(email, encoding="utf-8")
            metadata = {"project": "nea", "title": "Mark McCarthy SWE Interviews and Pod Retentions",
                        "date": "09/17/2026", "author": "Mark McCarthy", "enforce_worktree": False,
                        "allow_duplicate": True, "dry_run_graph": True,
                        "graph_path": str(root / "absent-preview.sqlite")}
            with patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", root), \
                    patch("dual_pipeline.canonical_pipeline.concurrency.ROOT", root), \
                    patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager"), \
                    patch("dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook",
                          return_value=ScoutHookResult(skipped_reason="test isolation")):
                result = DocumentPipeline(provider_type="offline").run(
                    source, metadata=metadata, output_dir=str(root / "output"), mutate_graph=False)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertTrue(Path(result["intake_package"]).is_file())
            receipt = json.loads(Path(result["execution_receipt"]).read_text())
            self.assertEqual(receipt["terminal_outcome"], "COMPLETE")
            self.assertEqual(set(receipt["stages"]), {stage.value for stage in PIPELINE_STAGE_SEQUENCE})
            self.assertTrue(all(stage["status"] == "PASS" for stage in receipt["stages"].values()))
            self.assertGreaterEqual(len(receipt["attempts"]), 1)
            self.assertTrue(all(attempt["status"] == "PASS" and attempt["duration_ms"] >= 0
                                for attempt in receipt["attempts"]))
            qa, judge, decision = (json.loads(Path(result[key]).read_text()) for key in
                                   ("qa_gates_report", "llm_judge_evaluation", "final_release_decision"))
            self.assertTrue(decision["authoritative_write_authorized"])
            graph = Path(result["output_03"]).read_text()
            verify_attempt_binding(SynthesisResult(output_3=graph),
                                   QAEvaluationResult(True, qa, judge, decision, True),
                                   email, CanonicalModality.DOCUMENT)
            self.assertFalse((root / "absent-preview.sqlite").exists())

    def test_document_pipeline_preflight_rejects_before_synthesis_judge_or_release(self):
        from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.docx"
            source.write_bytes(_docx_bytes())
            output = root / "output" / "project_lens" / "10-05-2026" / "Synthetic"
            metadata = {"project": "project_lens", "title": "x" * (envelope_field_limits()["title"] + 1),
                        "date": "10/05/2026", "enforce_worktree": False}
            with patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", root), \
                    patch("dual_pipeline.canonical_pipeline.concurrency.ROOT", root), \
                    patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager"), \
                    patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as judge:
                pipeline = DocumentPipeline(provider_type="offline")
                with patch.object(pipeline.provider, "synthesize_graph") as synthesis, \
                        patch.object(pipeline, "_execute_evals_release") as release:
                    with self.assertRaisesRegex(ValueError, "source.title"):
                        pipeline.run(source, metadata=metadata, output_dir=str(output), mutate_graph=False)
                synthesis.assert_not_called()
                judge.assert_not_called()
                release.assert_not_called()
            self.assertFalse(list(root.rglob("03_*canonical_signal_projection.json")))
            receipts = list((root / ".local/dual_pipeline/executions").glob("*/pipeline_execution_receipt.json"))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text())
            self.assertEqual(receipt["terminal_outcome"], "FAILED")
            self.assertEqual(receipt["stages"]["U0"]["status"], "FAILED")
            self.assertIsNotNone(receipt["execution_timing"]["finished_at"])
            self.assertTrue(receipt["evidence_digest"])


class Wave2LifecycleTests(DocumentResumeCases, unittest.TestCase):
    """Exact reuse after downstream failure; every current release check reruns."""


class Wave3LifecycleTests(DocumentW3Cases, DocumentExecutionCases, unittest.TestCase):
    """Bounded document work with unchanged quality and honest execution evidence."""


if __name__ == "__main__":
    unittest.main()
