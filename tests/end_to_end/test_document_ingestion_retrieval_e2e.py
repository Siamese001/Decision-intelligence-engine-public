"""W4 source-bound retrieval lifecycle acceptance on disposable SQLite copies."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.publish_helper import assemble_pipeline_result
from dual_pipeline.document_pipeline_core.retrieval_visibility import (
    RetrievalClearancePending, completion_from_publication, preflight_document_clearance,
    source_retrieval_completion,
)


def _graph(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, primary_source_id TEXT,
                currentness_state TEXT, retrieval_eligible INTEGER,
                canonical_status TEXT, validation_state TEXT,
                classification TEXT, allowed_audiences_json TEXT,
                assertion_type TEXT, origin_type TEXT, extraction_run_id TEXT,
                hitl_required INTEGER, validation_priority TEXT, validation_owner TEXT,
                atomicity_state TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT, evidence_relationship TEXT
            );
        """)
        rows = [
            ("a-eligible", "table-source", "current", 1, "candidate", "pending",
             "internal", '["internal_consulting"]', "fact", None, None, 0, None, None, None),
            ("a-restricted", "table-source", "current", 1, "candidate", "pending",
             "restricted", '["nee-engineering"]', "fact", None, None, 0, None, None, None),
            ("a-historical", "table-source", "historical", 1, "candidate", "pending",
             "internal", '["internal_consulting"]', "fact", None, None, 0, None, None, None),
            ("a-other", "email-source", "current", 1, "candidate", "pending",
             "internal", '["internal_consulting"]', "fact", None, None, 0, None, None, None),
        ]
        db.executemany("INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT INTO assertion_evidence VALUES (?, 'supports')",
                       [(row[0],) for row in rows])


def _sidecar(path: Path, *, changed: bool) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY, lane TEXT,
                retrieval_text_hash TEXT, artifact_json TEXT
            );
            CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY);
        """)
        entries = [
            ("r-table", "a-eligible", "table-source", "new" if changed else "old"),
            ("r-email", "a-other", "email-source", "email"),
        ]
        for record, assertion, source, text_hash in entries:
            artifact = {"extensions": {"com.partner_advisory.nee.source-id": source,
                                       "com.partner_advisory.nee.graph-object-id": assertion}}
            db.execute("INSERT INTO retrieval_record VALUES (?,?,?,?)",
                       (record, "candidate", text_hash, json.dumps(artifact)))
            db.execute("INSERT INTO dense_vector VALUES (?)", (record,))


def _evaluation() -> dict:
    cases = [
        {"scope": "delta", "query_kind": "semantic_paraphrase",
         "expected_record_ids": ["r-table"], "forbidden_record_ids": [],
         "candidate_retrieved_record_ids": ["r-table"]},
        {"scope": "delta", "query_kind": "acl_boundary",
         "expected_record_ids": [], "forbidden_record_ids": ["r-table"],
         "candidate_retrieved_record_ids": []},
        {"scope": "delta", "query_kind": "lane_isolation",
         "expected_record_ids": [], "forbidden_record_ids": ["r-table"],
         "candidate_retrieved_record_ids": []},
    ]
    return {"status": "PASS", "coverage": {"added_or_changed_record_ids": ["r-table"]},
            "case_results": cases}


class Wave4LifecycleTests(unittest.TestCase):
    def test_pending_retrieval_is_not_reported_as_complete(self):
        result = assemble_pipeline_result(
            CanonicalModality.DOCUMENT,
            {"retrieval_completion": {"status": "PUBLICATION_PENDING"}},
            Path("/temporary"), {}, None, Path("/temporary/package.json"),
            Path("/temporary/receipt.json"), {"status": "PREPARED_ACTION_AUTH_REQUIRED"},
        )
        self.assertEqual(result["status"], "RETRIEVAL_PENDING")
        self.assertEqual(result["retrieval_completion"]["status"], "PUBLICATION_PENDING")

    def test_restricted_client_conflict_stops_before_expensive_document_work(self):
        metadata = {"is_document": True, "classification": "restricted",
                    "audiences": ["internal_consulting", "nee-engineering"]}
        pipeline = object.__new__(CanonicalAgenticPipeline)
        pipeline._load_source_content = MagicMock(return_value=("table text", "a" * 64, None))
        with self.assertRaises(RetrievalClearancePending) as caught:
            pipeline._execute_u0(None, metadata, None, None, None, None,
                                 "auto", True, MagicMock())
        self.assertEqual(caught.exception.report["reason_code"],
                         "CONFIDENTIAL_RECORD_EXPOSED_TO_CLIENT_AUDIENCE")
        self.assertEqual(metadata["classification"], "restricted")
        self.assertEqual(metadata["audiences"], ["internal_consulting", "nee-engineering"])
        self.assertFalse(metadata["retrieval_preflight"]["labels_changed"])

    def test_operator_decision_remains_pending_without_relabeling(self):
        metadata = {"classification": "confidential",
                    "audiences": ["internal_consulting", "nee-engineering"]}
        report = preflight_document_clearance(metadata, required=False)
        self.assertEqual(report["status"], "PENDING_OPERATOR_DECISION")
        with self.assertRaises(RetrievalClearancePending):
            preflight_document_clearance(metadata, required=True)
        self.assertEqual(metadata["classification"], "confidential")
        self.assertEqual(metadata["audiences"], ["internal_consulting", "nee-engineering"])

    def test_source_visibility_requires_real_delta_hit_and_denial_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph, previous, current = (root / name for name in
                                         ("graph.sqlite", "previous.sqlite", "current.sqlite"))
            _graph(graph)
            _sidecar(previous, changed=False)
            _sidecar(current, changed=True)
            report = source_retrieval_completion(
                source_id="table-source", graph=graph, sidecar=current,
                previous_sidecar=previous, evaluation=_evaluation(),
                embedding_receipt={"delta_vectors": 1},
            )
            self.assertEqual(report["status"], "VERIFIED")
            self.assertEqual(report["eligible_assertion_count"], 1)
            self.assertEqual(report["exclusion_reasons"],
                             {"EXCLUDED_CLEARANCE_CONFIDENTIAL_RECORD_EXPOSED_TO_CLIENT_AUDIENCE": 1,
                              "EXCLUDED_HISTORICAL": 1})
            self.assertEqual(report["new_text_record_ids"], ["r-table"])
            self.assertEqual(report["acl_denied_record_ids"], ["r-table"])
            self.assertEqual(report["lane_denied_record_ids"], ["r-table"])
            self.assertFalse(report["candidate_promoted"])
            evaluation = _evaluation()
            evaluation["case_results"][0]["candidate_retrieved_record_ids"] = []
            denied = source_retrieval_completion(
                source_id="table-source", graph=graph, sidecar=current,
                previous_sidecar=previous, evaluation=evaluation,
                embedding_receipt={"delta_vectors": 1},
            )
            self.assertEqual(denied["status"], "SOURCE_RETRIEVAL_UNVERIFIED")
            unchanged = source_retrieval_completion(
                source_id="table-source", graph=graph, sidecar=previous,
                previous_sidecar=previous, evaluation=None,
                embedding_receipt={"delta_vectors": 0},
            )
            self.assertEqual(unchanged["status"], "UNCHANGED_TEXT")
            self.assertEqual(unchanged["new_text_record_ids"], [])

    def test_prepared_and_stale_generations_never_claim_completion(self):
        self.assertEqual(completion_from_publication({"status": "PREPARED_ACTION_AUTH_REQUIRED"},
                                                     Path("/unused"))["status"], "PUBLICATION_PENDING")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "active_pair.json").write_text(json.dumps(
                {"generation_id": "g-new", "graph_sha256": "a",
                 "sidecar_sha256": "b", "model_manifest_digest": "c"}))
            result = {"status": "COMPLETE", "release": {"receipt": {
                "next_pair": {"generation_id": "g-old", "graph_sha256": "a",
                              "sidecar_sha256": "b", "model_manifest_digest": "c"}}}}
            self.assertEqual(completion_from_publication(result, root)["status"],
                             "STALE_GENERATION")
