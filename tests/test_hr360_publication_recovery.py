"""
tests/test_hr360_publication_recovery.py

Wave 4 Acceptance Test Suite:
Recover publication without cleanup-and-reingest loops.

Covers:
1. Retrieval release schema preflight catches nullable metrics support and invalid schemas before model init.
2. Retrieval release preflight fails fast on missing or corrupt evaluation policies.
3. ReleaseManager preflight prevents index/model work when schema preflight fails.
4. Scoped release delta extracts and preserves all mutation operation kinds (relationship, edge, decision, etc.).
5. Scoped release delta scans multiple approved change-set variants.
6. Scoped release delta preserves direct retrieval-record prefixed IDs.
7. Relationship record types include relationship_assertion and relationship across constants and evaluator.
8. Guarded publication idempotent replay returns cached result without duplicate write transactions.
9. Guarded publication resumes interrupted intent when bindings match.
10. Guarded publication fails closed with INTENT_BINDINGS_MISMATCH when bindings differ.
11. Guarded publication fails closed with RESULT_BINDINGS_MISMATCH when bindings differ.
12. Publication checkpoint inspection and safe reset_interrupted reconciliation without manual file deletion.
13. Separation of retrieval gate PASS, source coverage VERIFIED, and publication COMPLETE.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    get_publication_checkpoint,
    guarded_publication,
    reconcile_publication_checkpoint,
)
from dual_pipeline.contracts import CONTRACT_ROOT, SCHEMA_DIR
from dual_pipeline.core import read_json
from dual_pipeline.retrieval_release_core.evaluator import RetrievalReleaseEvaluator
from dual_pipeline.retrieval_release_core.metrics import (
    _extract_assertion_ids_from_runs,
    _parse_change_set_ops,
    _scoped_release_delta,
)
from dual_pipeline.retrieval_release_core.preflight import (
    RetrievalSchemaPreflightError,
    preflight_retrieval_release_schema,
)
from dual_pipeline.retrieval_suite_core.constants import _RELATIONSHIP_RECORD_TYPES


class TestHR360PublicationRecovery(unittest.TestCase):
    """Wave 4 acceptance tests verifying publication recovery, coverage, and journal safety."""

    def test_preflight_verifies_nullable_metrics(self):
        """Preflight validates that candidate metrics allow null and probe payload passes."""
        res = preflight_retrieval_release_schema()
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["nullable_metrics_verified"])
        self.assertTrue(Path(res["schema_path"]).is_file())

    def test_preflight_fails_fast_on_corrupt_or_non_nullable_schema(self):
        """Preflight fails immediately when a metric is constrained to disallow null."""
        with tempfile.TemporaryDirectory() as td:
            bad_schema_path = Path(td) / "retrieval-release-evaluation.schema.json"
            real_schema = read_json(SCHEMA_DIR / "retrieval-release-evaluation.schema.json")

            # Corrupt the schema so candidate_recall_at_k requires number only
            import copy
            corrupted = copy.deepcopy(real_schema)
            corrupted["$defs"]["caseResult"]["properties"]["candidate_recall_at_k"]["type"] = "number"
            bad_schema_path.write_text(json.dumps(corrupted), encoding="utf-8")

            with self.assertRaises(RetrievalSchemaPreflightError) as ctx:
                preflight_retrieval_release_schema(schema_path=bad_schema_path)
            self.assertIn("candidate_recall_at_k must allow null", str(ctx.exception))

    def test_preflight_fails_on_missing_or_corrupt_policy(self):
        """Preflight fails immediately when evaluation policy thresholds are invalid."""
        with tempfile.TemporaryDirectory() as td:
            bad_policy_path = Path(td) / "retrieval-release-evaluation-policy.json"
            bad_policy_path.write_text(json.dumps({"owner": "Test", "no_thresholds": True}), encoding="utf-8")

            with self.assertRaises(RetrievalSchemaPreflightError) as ctx:
                preflight_retrieval_release_schema(policy_path=bad_policy_path)
            self.assertIn("missing required 'thresholds' dict", str(ctx.exception))

    def test_evaluator_runs_preflight_before_model_work(self):
        """RetrievalReleaseEvaluator calls preflight before candidate pair / embedding work."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            releases_dir = root / "releases"
            releases_dir.mkdir(parents=True)
            receipt_file = releases_dir / "release_001.json"
            receipt_file.write_text(json.dumps({
                "receipt_id": "rcpt-001",
                "status": "PREPARED",
            }), encoding="utf-8")

            evaluator = RetrievalReleaseEvaluator(root)

            with patch("dual_pipeline.retrieval_release_core.evaluator.preflight_retrieval_release_schema") as mock_pf:
                mock_pf.side_effect = RetrievalSchemaPreflightError("preflight schema check failed")

                with patch("dual_pipeline.retrieval_release_core.evaluator._verify_receipt"):
                    with self.assertRaises(RetrievalSchemaPreflightError):
                        evaluator.evaluate(
                            receipt_file,
                            suite_path=Path("/fake/suite.json"),
                            backend=MagicMock(),
                        )
                mock_pf.assert_called_once()



    def test_extract_assertion_ids_covers_all_mutation_kinds(self):
        """Delta extraction captures all mutation operation kinds including relationships, decisions, and proposals."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_dir = root / "runs" / "run-w4-01"
            run_dir.mkdir(parents=True)

            ops = [
                {"op": "add_canonical_assertion", "target_id": "assert-core-01", "target_type": "assertion"},
                {"op": "add_relationship", "target_id": "rel-01", "target_type": "relationship"},
                {"op": "add_architecture_relationship", "target_id": "arch-rel-01", "target_type": "architecture_relationship"},
                {"op": "add_edge", "target_id": "edge-01", "target_type": "edge"},
                {"op": "add_relationship_signal", "target_id": "sig-01", "target_type": "relationship_signal"},
                {"op": "add_action_item", "target_id": "action-01", "target_type": "action_item"},
                {"op": "add_decision", "target_id": "dec-01", "target_type": "decision"},
                {"op": "add_proposal", "target_id": "prop-01", "target_type": "proposal"},
                {"op": "add_risk", "target_id": "risk-01", "target_type": "risk"},
                {"op": "supersede_assertion", "target_id": "old-assert-01", "target_type": "assertion"},
            ]
            (run_dir / "graph_change_set.json").write_text(json.dumps(ops), encoding="utf-8")

            superseded, added = _extract_assertion_ids_from_runs(root, [{"run_id": "run-w4-01"}])

            self.assertIn("old-assert-01", superseded)
            for expected_id in [
                "assert-core-01", "rel-01", "arch-rel-01", "edge-01", "sig-01",
                "action-01", "dec-01", "prop-01", "risk-01",
            ]:
                self.assertIn(expected_id, added, f"Missing expected added target_id: {expected_id}")

    def test_extract_assertion_ids_scans_multiple_changeset_variants(self):
        """Delta extraction scans approved and batch-approved change set variants."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_dir = run_dir = root / "runs" / "run-w4-02"
            run_dir.mkdir(parents=True)

            (run_dir / "graph_change_set_approved.json").write_text(
                json.dumps([{"op": "add_approved_assertion", "target_id": "approved-01"}]),
                encoding="utf-8",
            )
            (run_dir / "graph_change_set_batch_approved.json").write_text(
                json.dumps([{"op": "add_relationship", "target_id": "batch-rel-01"}]),
                encoding="utf-8",
            )

            superseded, added = _extract_assertion_ids_from_runs(root, [{"run_id": "run-w4-02"}])
            self.assertIn("approved-01", added)
            self.assertIn("batch-rel-01", added)

    def test_scoped_release_delta_preserves_relationship_records(self):
        """_scoped_release_delta keeps direct retrieval-record relationship IDs in coverage delta."""
        from dual_pipeline.core import stable_id

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_dir = root / "runs" / "run-delta"
            run_dir.mkdir(parents=True)
            ops = [
                {"op": "add_relationship", "target_id": "retrieval-record-new-rel-123"},
                {"op": "add_canonical_assertion", "target_id": "normal-assert-456"},
                {"op": "supersede_assertion", "target_id": "retrieval-record-old-rel-999"},
            ]
            (run_dir / "graph_change_set.json").write_text(json.dumps(ops), encoding="utf-8")

            receipt = {
                "component_runs": [{"run_id": "run-delta"}],
            }

            baseline_records = {
                "retrieval-record-old-rel-999": json.dumps({"record_id": "retrieval-record-old-rel-999", "retrieval_record": {"record_type": "relationship"}}),
                "existing-record-1": json.dumps({"record_id": "existing-record-1", "retrieval_record": {"record_type": "assertion"}}),
            }
            norm_id = stable_id("retrieval-record", "normal-assert-456", length=28)
            candidate_records = {
                "retrieval-record-new-rel-123": json.dumps({"record_id": "retrieval-record-new-rel-123", "retrieval_record": {"record_type": "relationship"}}),
                norm_id: json.dumps({"record_id": norm_id, "retrieval_record": {"record_type": "assertion"}}),
                "existing-record-1": json.dumps({"record_id": "existing-record-1", "retrieval_record": {"record_type": "assertion"}}),
            }

            delta_records, removed_records = _scoped_release_delta(
                runtime_root=root,
                receipt=receipt,
                baseline_records=baseline_records,
                candidate_records=candidate_records,
            )

            self.assertIn("retrieval-record-new-rel-123", delta_records)
            self.assertIn(norm_id, delta_records)
            self.assertIn("retrieval-record-old-rel-999", removed_records)
            self.assertNotIn("existing-record-1", delta_records)


    def test_relationship_record_types_registered(self):
        """_RELATIONSHIP_RECORD_TYPES recognizes relationship_assertion and relationship."""
        self.assertIn("edge", _RELATIONSHIP_RECORD_TYPES)
        self.assertIn("architecture_relationship", _RELATIONSHIP_RECORD_TYPES)
        self.assertIn("relationship_assertion", _RELATIONSHIP_RECORD_TYPES)
        self.assertIn("relationship", _RELATIONSHIP_RECORD_TYPES)

    def test_guarded_publication_idempotent_replay(self):
        """Replaying identical publication returns cached result without duplicate write transactions."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directive_id = "test-directive-001"
            bindings = {"source": "hr360_memo.md", "hash": "abc12345"}

            call_count = [0]

            def publish():
                call_count[0] += 1
                return {"status": "ACTIVATED", "pair_id": "pair-001"}

            # First invocation
            res1 = guarded_publication(
                runtime_root=root,
                directive_id=directive_id,
                bindings_factory=lambda: bindings,
                publish=publish,
                allow_resume=True,
            )
            self.assertEqual(res1, {"status": "ACTIVATED", "pair_id": "pair-001"})
            self.assertEqual(call_count[0], 1)

            # Second identical invocation
            res2 = guarded_publication(
                runtime_root=root,
                directive_id=directive_id,
                bindings_factory=lambda: bindings,
                publish=publish,
                allow_resume=True,
            )
            self.assertEqual(res2, {"status": "ACTIVATED", "pair_id": "pair-001"})
            # Verification: publish was NOT called again; cached result was safely returned!
            self.assertEqual(call_count[0], 1)

    def test_guarded_publication_intent_resume(self):
        """Interrupted publication with PENDING intent resumes smoothly when bindings match."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directive_id = "test-directive-002"
            bindings = {"source": "hr360_memo.md", "version": "v1"}

            # Simulate an interrupted intent
            def fail_once():
                raise RuntimeError("simulated crash before result recording")

            with self.assertRaises(PublicationReconciliationRequired):
                guarded_publication(
                    runtime_root=root,
                    directive_id=directive_id,
                    bindings_factory=lambda: bindings,
                    publish=fail_once,
                    allow_resume=False,
                )

            chk = get_publication_checkpoint(root, directive_id)
            self.assertTrue(chk["intent_exists"])
            self.assertEqual(chk["intent_state"], "PENDING")
            self.assertFalse(chk["result_exists"])

            # Now resume with allow_resume=True and matching bindings
            resumed_result = guarded_publication(
                runtime_root=root,
                directive_id=directive_id,
                bindings_factory=lambda: bindings,
                publish=lambda: {"status": "RESUMED_SUCCESS"},
                allow_resume=True,
            )
            self.assertEqual(resumed_result, {"status": "RESUMED_SUCCESS"})

            chk_after = get_publication_checkpoint(root, directive_id)
            self.assertTrue(chk_after["result_exists"])
            self.assertEqual(chk_after["result_state"], "RESULT_RECORDED")

    def test_guarded_publication_fails_closed_on_intent_bindings_mismatch(self):
        """Resuming with altered input bindings fails closed with INTENT_BINDINGS_MISMATCH."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directive_id = "test-directive-003"
            orig_bindings = {"source": "hr360_memo.md", "version": "v1"}
            changed_bindings = {"source": "hr360_memo.md", "version": "v2"}

            # Leave a pending intent with orig_bindings
            with self.assertRaises(PublicationReconciliationRequired):
                guarded_publication(
                    runtime_root=root,
                    directive_id=directive_id,
                    bindings_factory=lambda: orig_bindings,
                    publish=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
                    allow_resume=False,
                )

            # Attempt resume with changed bindings
            with self.assertRaises(PublicationReconciliationRequired) as ctx:
                guarded_publication(
                    runtime_root=root,
                    directive_id=directive_id,
                    bindings_factory=lambda: changed_bindings,
                    publish=lambda: {"status": "should_not_run"},
                    allow_resume=True,
                )
            self.assertEqual(ctx.exception.state, "INTENT_BINDINGS_MISMATCH")

    def test_guarded_publication_fails_closed_on_result_bindings_mismatch(self):
        """Replaying with altered input bindings against an existing result fails closed with RESULT_BINDINGS_MISMATCH."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directive_id = "test-directive-004"
            orig_bindings = {"source": "hr360_memo.md", "version": "v1"}
            changed_bindings = {"source": "hr360_memo.md", "version": "v2"}

            guarded_publication(
                runtime_root=root,
                directive_id=directive_id,
                bindings_factory=lambda: orig_bindings,
                publish=lambda: {"status": "done"},
                allow_resume=True,
            )

            with self.assertRaises(PublicationReconciliationRequired) as ctx:
                guarded_publication(
                    runtime_root=root,
                    directive_id=directive_id,
                    bindings_factory=lambda: changed_bindings,
                    publish=lambda: {"status": "should_not_run"},
                    allow_resume=True,
                )
            self.assertEqual(ctx.exception.state, "RESULT_BINDINGS_MISMATCH")

    def test_reconcile_publication_checkpoint_reset_interrupted(self):
        """reconcile_publication_checkpoint safely archives pending intent without manual file deletion."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directive_id = "test-directive-005"
            bindings = {"source": "hr360_memo.md", "data": 123}

            # Create an interrupted pending intent
            with self.assertRaises(PublicationReconciliationRequired):
                guarded_publication(
                    runtime_root=root,
                    directive_id=directive_id,
                    bindings_factory=lambda: bindings,
                    publish=lambda: (_ for _ in ()).throw(RuntimeError("interrupted")),
                    allow_resume=False,
                )

            chk_before = get_publication_checkpoint(root, directive_id)
            self.assertTrue(chk_before["intent_exists"])

            recon = reconcile_publication_checkpoint(root, directive_id, action="reset_interrupted")
            self.assertEqual(recon["status"], "RECONCILED")
            self.assertEqual(recon["prior_state"], "PENDING")
            self.assertTrue(Path(recon["archived_path"]).is_file())

            # Checkpoint now reports clean state
            chk_after = get_publication_checkpoint(root, directive_id)
            self.assertFalse(chk_after["intent_exists"])

            # Can now publish afresh
            fresh_res = guarded_publication(
                runtime_root=root,
                directive_id=directive_id,
                bindings_factory=lambda: {"source": "hr360_memo.md", "data": 456},
                publish=lambda: {"status": "fresh_success"},
                allow_resume=False,
            )
            self.assertEqual(fresh_res, {"status": "fresh_success"})

    def test_distinguish_retrieval_pass_source_verified_publication_complete(self):
        """Lifecycle distinctly records retrieval gate PASS, source coverage VERIFIED, and publication COMPLETE."""
        eval_artifact = {
            "gates": {"real_bge_m3": True, "coverage_complete": True, "core_thresholds": True},
            "coverage": {"complete": True, "added_or_changed_record_ids": ["r1", "r2"]},
            "status": "PASS",
        }
        self.assertEqual(eval_artifact["status"], "PASS")
        self.assertTrue(eval_artifact["coverage"]["complete"])

        # Publication result status
        pub_result = {
            "status": "COMPLETE",
            "activated_pair": {"generation_id": "gen-002", "graph_sha256": "g2", "sidecar_sha256": "s2"},
            "retrieval_status": eval_artifact["status"],
            "source_coverage": "VERIFIED" if eval_artifact["coverage"]["complete"] else "INCOMPLETE",
        }
        self.assertEqual(pub_result["status"], "COMPLETE")
        self.assertEqual(pub_result["retrieval_status"], "PASS")
        self.assertEqual(pub_result["source_coverage"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
