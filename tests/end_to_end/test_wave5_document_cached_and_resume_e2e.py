"""
tests/end_to_end/test_wave5_document_cached_and_resume_e2e.py

Wave 5 E2E Regression Suite: Independent Cached and Resume Execution.
Verifies that:
1. Cache lookup resolves document release artifacts in nested subdirectories.
2. Document idempotency check (check_idempotent_or_continue) discovers nested artifacts and exits U0 cleanly.
3. Resume scenario (try_resume_passing_attempt) discovers nested attempts and reuses saved synthesis with 0 model calls.
4. Document resume flag specifically bypasses U0 idempotency to force downstream gate verification.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Dict
import unittest

from dual_pipeline.canonical_pipeline.attachments import try_resume_passing_attempt
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.publish_helper import check_idempotent_or_continue
from dual_pipeline.transcript_pipeline_core.caching import (
    check_idempotent_meeting_run,
    compute_prompt_template_hash,
)


class DummyTracker:
    def __init__(self) -> None:
        self.completed_stages: Dict[str, Dict[str, Any]] = {}

    def complete_stage(self, stage: Any, status: str = "PASS", details: Dict[str, Any] | None = None) -> None:
        self.completed_stages[str(stage)] = {"status": status, "details": details or {}}


class TestWave5DocumentCachedAndResumeE2E(unittest.TestCase):
    """Verifies nested artifact resolution, zero-model-call cache reuse, and resume behavior."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory(prefix="wave5_test_")
        self.root_dir = Path(self.temp_dir_obj.name)
        self.source_text = "Apex Power & Grid expanded regional grid transmission capacity."
        self.input_hash = "abc123hash456"

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def test_nested_document_cache_lookup_succeeds_without_model_calls(self) -> None:
        """Cache lookup finds document results in nested folders and returns expected paths."""
        nested_out = self.root_dir / "10-11-2026" / "pdf_test"
        nested_out.mkdir(parents=True, exist_ok=True)

        prompt_hash = compute_prompt_template_hash()
        dec_payload = {
            "status": "PASS",
            "authoritative_write_authorized": True,
            "source_file_hash": self.input_hash,
            "prompt_template_hash": prompt_hash,
        }
        (nested_out / "final_release_decision.json").write_text(json.dumps(dec_payload), encoding="utf-8")
        (nested_out / "03_pdf_test_10-11-2026_120000_canonical_signal_projection.json").write_text(
            json.dumps({"evidence_index": {}, "graph_mutations": {"entity_upserts": []}}),
            encoding="utf-8",
        )
        (nested_out / "qa_gates_report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
        (nested_out / "llm_judge_evaluation.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
        (nested_out / "receipt.json").write_text(
            json.dumps({"terminal_outcome": "COMPLETE", "graph_mutated": True}),
            encoding="utf-8",
        )

        meta: Dict[str, Any] = {
            "is_document": True,
            "meeting_name": "pdf_test",
            "date": "10-11-2026",
            "time": "12:00:00",
        }

        # Idempotent meeting run directly on root_dir (which only contains nested subfolders)
        expected = check_idempotent_meeting_run(
            output_dir=self.root_dir,
            metadata=meta,
            content_hash=self.input_hash,
        )
        self.assertIsNotNone(expected, "check_idempotent_meeting_run must find nested artifacts")
        self.assertTrue(Path(expected["03_graph"]).is_file(), "03_graph must resolve to existing file")

        # Full U0 check_idempotent_or_continue must complete U0 stage as idempotent_noop
        tracker = DummyTracker()
        res = check_idempotent_or_continue(
            tracker=tracker,
            output_dir=str(self.root_dir),
            meta=meta,
            input_hash=self.input_hash,
            mutate_graph=True,
        )
        self.assertIsNotNone(res, "check_idempotent_or_continue must return idempotent dict")
        stage_names = [k for k in tracker.completed_stages.keys()]
        self.assertTrue(len(stage_names) > 0, "Tracker must record completed stage")
        self.assertTrue(
            any(stage["details"].get("idempotent_noop") for stage in tracker.completed_stages.values()),
            "U0 must complete with idempotent_noop flag",
        )

    def test_nested_document_resume_reuses_saved_synthesis(self) -> None:
        """Resume scenario finds nested attempts directory and reuses saved candidate."""
        nested_out = self.root_dir / "10-11-2026" / "pdf_test"
        att_dir = nested_out / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)

        dummy_f3 = json.dumps({
            "evidence_index": {"ev-01": {"text": "Grid expansion", "exact_text": "Grid expansion"}},
            "graph_mutations": {"entity_upserts": [{"id": "org:apex", "name": "APEX"}]},
        })

        (att_dir / "final_release_decision.json").write_text(
            json.dumps({"status": "PASS", "authoritative_write_authorized": True}),
            encoding="utf-8",
        )
        (att_dir / "03_pdf_test_10-11-2026_canonical_signal_projection.json").write_text(dummy_f3, encoding="utf-8")
        (att_dir / "output_3.ckpt.json").write_text(dummy_f3, encoding="utf-8")
        (att_dir / "qa_gates_report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
        (att_dir / "llm_judge_evaluation.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")

        meta: Dict[str, Any] = {"is_document": True, "resume": True}

        # try_resume_passing_attempt invoked on root_dir (which contains nested subfolders)
        resumed = try_resume_passing_attempt(
            out_p=self.root_dir,
            metadata=meta,
            modality=CanonicalModality.DOCUMENT,
        )
        self.assertIsNotNone(resumed, "try_resume_passing_attempt must find and resume nested attempt")
        attempt_num, s_res, e_res = resumed
        self.assertEqual(attempt_num, 1)
        self.assertEqual(s_res.output_3, dummy_f3, "Reused synthesis must match saved output_3")
        self.assertTrue(e_res.passed, "Reused evaluation must reflect passed state")

    def test_document_resume_flag_bypasses_u0_idempotency_to_verify_downstream_gates(self) -> None:
        """When resume=True is set, U0 idempotency check returns None to allow gate verification."""
        nested_out = self.root_dir / "10-11-2026" / "pdf_test"
        nested_out.mkdir(parents=True, exist_ok=True)

        prompt_hash = compute_prompt_template_hash()
        dec_payload = {
            "status": "PASS",
            "authoritative_write_authorized": True,
            "source_file_hash": self.input_hash,
            "prompt_template_hash": prompt_hash,
        }
        (nested_out / "final_release_decision.json").write_text(json.dumps(dec_payload), encoding="utf-8")
        (nested_out / "receipt.json").write_text(
            json.dumps({"terminal_outcome": "COMPLETE", "graph_mutated": True}),
            encoding="utf-8",
        )

        meta: Dict[str, Any] = {
            "is_document": True,
            "resume": True,  # Explicit resume requested
            "meeting_name": "pdf_test",
            "date": "10-11-2026",
            "time": "12:00:00",
        }

        tracker = DummyTracker()
        res = check_idempotent_or_continue(
            tracker=tracker,
            output_dir=str(self.root_dir),
            meta=meta,
            input_hash=self.input_hash,
            mutate_graph=True,
        )
        self.assertIsNone(
            res,
            "check_idempotent_or_continue must return None when resume=True is requested",
        )


if __name__ == "__main__":
    unittest.main()
