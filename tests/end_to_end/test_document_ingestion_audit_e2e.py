"""
tests/end_to_end/test_document_ingestion_audit_e2e.py

Wave 5 Lifecycle Tests:
Verify measured removal of audit and publication duplication:
- Candidate healing memoization and invalidation.
- Release pair validation caching on immutable bindings.
- Exact plan selection and output artifact review exclusion.
- Concrete before/after counters and timing telemetry.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.document_pipeline_core.audit_dedup import (
    get_healing_counters,
    healed_output_3_cached,
    invalidate_candidate_healing_cache,
    reset_healing_counters,
)
from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_judge_and_release
from dual_pipeline.release_core.manager import ReleaseManager
from tools.semantic_diff_reviewer import LogicDetective, PlanFidelityAuditor, _read_plan_content


class Wave5LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_healing_counters()

    def tearDown(self) -> None:
        reset_healing_counters()

    def test_healing_deduplication_lifecycle(self) -> None:
        """Verify that document evaluation executes healing once and reuses memoized result."""
        candidate = json.dumps({
            "assertions": [
                {
                    "assertion_id": "A1",
                    "subject": "System",
                    "predicate": "implements",
                    "object": "Deduplication",
                    "confidence": 0.95,
                    "citation": "Source paragraph 1",
                }
            ],
            "entities": [{"entity_id": "E1", "name": "System", "type": "System"}],
        })
        source_text = "The System implements Deduplication across all document layers."
        metadata = {"source_name": "lens_doc_scope", "modality": "document"}

        counters_before = get_healing_counters()
        self.assertEqual(counters_before["raw_calls"], 0)

        # Run complete document QA and judge evaluation
        qa_report, j3_eval, decision = evaluate_document_judge_and_release(
            candidate,
            source_text=source_text,
            metadata=metadata,
            mock=True,
            fast_judge=True,
        )

        counters_after = get_healing_counters()
        # Evaluator calls healing at outer level, then inner Gate 3 calls it
        self.assertGreaterEqual(counters_after["raw_calls"], 2)
        self.assertEqual(counters_after["executions"], 1)
        self.assertGreaterEqual(counters_after["cache_hits"], 1)
        self.assertGreaterEqual(counters_after["saved_calls"], 1)
        self.assertIn(qa_report["status"], ("PASS", "FAIL"))

    def test_candidate_mutation_invalidates_cache(self) -> None:
        """Verify that candidate modification produces a distinct digest and triggers execution."""
        candidate_a = json.dumps({"assertions": [{"assertion_id": "A1", "subject": "Alpha"}]})
        candidate_b = json.dumps({"assertions": [{"assertion_id": "A2", "subject": "Beta"}]})

        res_a, _ = healed_output_3_cached(candidate_a)
        counters_a = get_healing_counters()
        self.assertEqual(counters_a["executions"], 1)

        # Candidate B must miss cache and execute healing
        res_b, _ = healed_output_3_cached(candidate_b)
        counters_b = get_healing_counters()
        self.assertEqual(counters_b["executions"], 2)
        self.assertNotEqual(res_a, res_b)

        # Re-querying candidate A must hit cache
        res_a2, _ = healed_output_3_cached(candidate_a)
        counters_a2 = get_healing_counters()
        self.assertEqual(counters_a2["executions"], 2)
        self.assertEqual(counters_a2["cache_hits"], 1)
        self.assertEqual(res_a, res_a2)

    def test_pair_validation_deduplication(self) -> None:
        """Verify that ReleaseManager.validate_pair reuses validation result on identical pair."""
        manager = ReleaseManager.__new__(ReleaseManager)
        manager._validated_pairs = {}
        manager._resolve = lambda p: Path(tempfile.gettempdir()) / Path(p).name

        test_file = Path(tempfile.gettempdir()) / "test_dummy.sqlite"
        test_file.write_bytes(b"dummy graph bytes")

        pair = {
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "production_qualified": False,
            "graph_path": str(test_file),
            "sidecar_path": str(test_file),
            "graph_sha256": "dummy_sha",
            "sidecar_sha256": "dummy_sha",
            "generation_id": "gen-test-01",
            "graph_snapshot": "snapshot_test_01",
        }

        # Mock expensive checks
        with patch("dual_pipeline.release_core.manager.require_approved_bge_m3_pair"), \
             patch("dual_pipeline.release_core.manager.sha256_file", return_value="dummy_sha"), \
             patch("dual_pipeline.release_core.manager.sqlite_integrity", return_value={"integrity_check": "ok", "foreign_key_violation_count": 0}), \
             patch("dual_pipeline.release_core.manager.validate_sidecar", return_value={"metadata": {
                 "embedding_backend": "flagembedding-bge-m3-dev",
                 "model_id": "BAAI/bge-m3",
                 "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
                 "production_qualified": "false",
                 "model_manifest_digest": "not-applicable",
             }}), \
             patch("dual_pipeline.release_core.manager.audit_graph_labels", return_value={"status": "PASS", "blocking_codes": []}) as mock_audit:

            res1 = manager.validate_pair(pair)
            self.assertEqual(res1["status"], "PASS")
            self.assertEqual(mock_audit.call_count, 1)

            # Second call with same pair must hit cache without calling audit_graph_labels
            res2 = manager.validate_pair(pair)
            self.assertEqual(res2["status"], "PASS")
            self.assertEqual(mock_audit.call_count, 1)

    def test_pair_mutation_invalidates_cache(self) -> None:
        """Verify that altering graph or generation in pair invalidates cached validation."""
        manager = ReleaseManager.__new__(ReleaseManager)
        manager._validated_pairs = {}
        manager._resolve = lambda p: Path(tempfile.gettempdir()) / Path(p).name

        test_file = Path(tempfile.gettempdir()) / "test_dummy2.sqlite"
        test_file.write_bytes(b"dummy graph bytes 2")

        pair_a = {
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "production_qualified": False,
            "graph_path": str(test_file),
            "sidecar_path": str(test_file),
            "graph_sha256": "sha_a",
            "sidecar_sha256": "sha_a",
            "generation_id": "gen-a",
            "graph_snapshot": "snapshot_a",
        }
        pair_b = dict(pair_a, generation_id="gen-b", graph_sha256="sha_b", sidecar_sha256="sha_b")

        with patch("dual_pipeline.release_core.manager.require_approved_bge_m3_pair"), \
             patch("dual_pipeline.release_core.manager.sha256_file", side_effect=lambda p: "sha_a" if "sha_a" in str(p) or pair_a["generation_id"] == "gen-a" else "sha_b"), \
             patch("dual_pipeline.release_core.manager.sqlite_integrity", return_value={"integrity_check": "ok", "foreign_key_violation_count": 0}), \
             patch("dual_pipeline.release_core.manager.validate_sidecar", return_value={"metadata": {
                 "embedding_backend": "flagembedding-bge-m3-dev",
                 "model_id": "BAAI/bge-m3",
                 "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
                 "production_qualified": "false",
                 "model_manifest_digest": "not-applicable",
             }}), \
             patch("dual_pipeline.release_core.manager.audit_graph_labels", return_value={"status": "PASS", "blocking_codes": []}) as mock_audit:

            manager.validate_pair(pair_a)
            self.assertEqual(mock_audit.call_count, 1)

            # Pair B has different generation/sha; must execute fresh validation
            with patch("dual_pipeline.release_core.manager.sha256_file", return_value="sha_b"):
                manager.validate_pair(pair_b)
                self.assertEqual(mock_audit.call_count, 2)

    def test_exact_plan_binding_resolution(self) -> None:
        """Verify that _read_plan_content binds strictly to exact slug and not arbitrary keyword matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            plans_dir = tmproot / "docs" / "plans"
            plans_dir.mkdir(parents=True)

            plan_unrelated = plans_dir / "WAVE_PLAN_INGEST_ENTERPRISE4U_ALIGN_SOW_AND_PRF_SCOPE.md"
            plan_unrelated.write_text("# Unrelated ENTERPRISE4U Plan\nDeliverables: `foo.py`")

            plan_target = plans_dir / "feat_ingest_project_lens_sow_phase1_scope.md"
            plan_target.write_text("# Target LENS Plan\nDeliverables: `lens.py`")

            content = _read_plan_content(tmproot, "feat/ingest-project-lens-sow-phase1-scope")
            self.assertIn("# Target LENS Plan", content)
            self.assertNotIn("# Unrelated ENTERPRISE4U Plan", content)

    def test_output_artifacts_excluded_from_code_review(self) -> None:
        """Verify that output/ production artifacts are excluded from LogicDetective and PlanFidelity."""
        detective = LogicDetective()
        added_lines = {
            "output/project_lens/10-02-2026/Scope/source_document.py": [
                (1, "except Exception: pass"),
            ],
            "dual_pipeline/clean_module.py": [
                (1, "x = 42"),
            ],
        }
        findings, _ = detective.audit(added_lines)
        self.assertEqual(len(findings), 0, "output/ directory must not trigger code logic findings")

        auditor = PlanFidelityAuditor()
        diff_files = [
            "dual_pipeline/clean_module.py",
            "output/project_lens/10-02-2026/Scope/source_document.txt",
        ]
        plan_text = "Planned files: `dual_pipeline/clean_module.py`, `output/project_lens/10-02-2026/Scope/source_document.txt`"
        score, _, summary = auditor.audit(diff_files, plan_text)
        self.assertEqual(score, 1.0)
        self.assertEqual(summary["declared_deliverables_count"], 1)
        self.assertNotIn("output/project_lens/10-02-2026/Scope/source_document.txt", summary["matched_files"])

    def test_measured_counters_and_timings_recorded(self) -> None:
        """Verify before/after counters and elapsed time telemetry are populated."""
        candidate = json.dumps({"assertions": []})
        healed_output_3_cached(candidate)
        healed_output_3_cached(candidate)

        counters = get_healing_counters()
        self.assertEqual(counters["raw_calls"], 2)
        self.assertEqual(counters["cache_hits"], 1)
        self.assertEqual(counters["executions"], 1)
        self.assertEqual(counters["saved_calls"], 1)
        self.assertIsInstance(counters["elapsed_saved_sec"], float)
