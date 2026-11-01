"""W4 batch admission and retrieval false-positive adversaries."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import sha256_file
from dual_pipeline.document_pipeline_core.publisher import publish_document_batch_to_graph
from dual_pipeline.document_pipeline_core.retrieval_visibility import RetrievalClearancePending
from dual_pipeline.document_pipeline_core.retrieval_visibility import (
    completion_from_publication, source_retrieval_completion,
)
from tests.end_to_end.test_document_ingestion_retrieval_e2e import _evaluation, _graph, _sidecar


class Wave4AdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.table = self.root / "table.json"
        self.email = self.root / "email.json"
        self.table.write_text(json.dumps({"meeting": {
            "meeting_id": "table-1", "source_hash": "table-hash",
            "classification": "internal", "audiences": ["internal_consulting"]}}))
        self.email.write_text(json.dumps({"meeting": {
            "meeting_id": "email-1", "source_hash": "email-hash",
            "classification": "internal", "audiences": ["internal_consulting"]}}))
        self.manifest = self.root / "batch.json"
        self.manifest.write_text("{}")
        self.rows = [{"package_file_sha256": sha256_file(path)}
                     for path in (self.table, self.email)]
        self.kwargs = {"directive_id": "directive-test", "batch_manifest": self.manifest,
                       "reviewer": "operator", "rationale": "joint acceptance",
                       "dependencies": {"email-1": "table-1"}}

    def _publish(self, packages):
        with patch("dual_pipeline.batch_admission.load_manifest",
                   return_value=({"packages": self.rows}, None)), \
                patch("dual_pipeline.cli.handlers._intake_publish",
                      return_value={"status": "HITL_REQUIRED"}) as intake:
            result = publish_document_batch_to_graph(packages, **self.kwargs)
        return result, intake

    def test_batch_submits_exact_order_once_and_preserves_pending_approval(self):
        result, intake = self._publish([(self.table, "table"), (self.email, "email")])
        self.assertEqual(result["status"], "HITL_REQUIRED")
        self.assertEqual(result["source_order"], ["table-1", "email-1"])
        self.assertEqual(result["source_dependencies"], {"email-1": "table-1"})
        intake.assert_called_once()
        args = intake.call_args.args[0]
        self.assertEqual(args.package, [self.table.resolve(), self.email.resolve()])
        self.assertEqual(args.batch_manifest, self.manifest.resolve())
        self.assertEqual(args.reviewer, "operator")

    def test_rejects_duplicate_reversed_or_unbound_batches_before_intake(self):
        with patch("dual_pipeline.batch_admission.load_manifest",
                   return_value=({"packages": self.rows}, None)), \
                patch("dual_pipeline.cli.handlers._intake_publish") as intake:
            for packages in (
                [(self.email, "email"), (self.table, "table")],
                [(self.table, "table"), (self.table, "email")],
                [(self.table, "table"), (self.email, "table")],
            ):
                with self.subTest(packages=packages), self.assertRaises(ValueError):
                    publish_document_batch_to_graph(packages, **self.kwargs)
            with self.assertRaisesRegex(ValueError, "dependency"):
                publish_document_batch_to_graph(
                    [(self.table, "table"), (self.email, "email")],
                    **{**self.kwargs, "dependencies": {"email-1": "missing"}})
            intake.assert_not_called()
        with patch("dual_pipeline.batch_admission.load_manifest",
                   return_value=({"packages": self.rows[:1]}, None)), \
                patch("dual_pipeline.cli.handlers._intake_publish") as intake:
            with self.assertRaisesRegex(ValueError, "exact package set"):
                publish_document_batch_to_graph(
                    [(self.table, "table"), (self.email, "email")], **self.kwargs)
            intake.assert_not_called()

    def test_restricted_client_batch_stops_before_model_or_intake(self):
        payload = json.loads(self.email.read_text())
        payload["meeting"].update(classification="restricted",
                                  audiences=["nee-engineering"])
        self.email.write_text(json.dumps(payload))
        self.rows = [{"package_file_sha256": sha256_file(path)}
                     for path in (self.table, self.email)]
        with patch("dual_pipeline.batch_admission.load_manifest",
                   return_value=({"packages": self.rows}, None)), \
                patch("dual_pipeline.cli.handlers._intake_publish") as intake:
            with self.assertRaises(RetrievalClearancePending):
                publish_document_batch_to_graph(
                    [(self.table, "table"), (self.email, "email")], **self.kwargs)
            intake.assert_not_called()

    def test_missing_delta_embedding_or_denial_case_cannot_be_verified(self):
        graph = self.root / "graph.sqlite"
        previous = self.root / "previous.sqlite"
        current = self.root / "current.sqlite"
        _graph(graph)
        _sidecar(previous, changed=False)
        _sidecar(current, changed=True)
        common = dict(source_id="table-source", graph=graph, sidecar=current,
                      previous_sidecar=previous)
        self.assertEqual(source_retrieval_completion(
            **common, evaluation=_evaluation(),
            embedding_receipt={"delta_vectors": 0})["status"],
            "EMBEDDING_DELTA_UNVERIFIED")
        evaluation = _evaluation()
        evaluation["case_results"] = evaluation["case_results"][:2]
        self.assertEqual(source_retrieval_completion(
            **common, evaluation=evaluation,
            embedding_receipt={"delta_vectors": 1})["status"],
            "SOURCE_RETRIEVAL_UNVERIFIED")
        self.assertEqual(completion_from_publication(
            {"status": "COMPLETE"}, self.root)["status"], "NO_NEW_TEXT_UNVERIFIED")
        evaluation = _evaluation()
        evaluation["case_results"][1]["candidate_retrieved_record_ids"] = ["r-table"]
        self.assertEqual(source_retrieval_completion(
            **common, evaluation=evaluation,
            embedding_receipt={"delta_vectors": 1})["status"],
            "SOURCE_RETRIEVAL_UNVERIFIED")

    def test_candidate_cannot_be_silently_promoted_to_approved_lane(self):
        import sqlite3
        graph = self.root / "graph.sqlite"
        current = self.root / "current.sqlite"
        _graph(graph)
        _sidecar(current, changed=True)
        with sqlite3.connect(current) as connection:
            connection.execute("UPDATE retrieval_record SET lane='approved' WHERE record_id='r-table'")
        report = source_retrieval_completion(
            source_id="table-source", graph=graph, sidecar=current,
            previous_sidecar=None, evaluation=_evaluation(),
            embedding_receipt={"delta_vectors": 1},
        )
        self.assertEqual(report["status"], "UNAUTHORIZED_LANE_PROMOTION")
        self.assertTrue(report["candidate_promoted"])
