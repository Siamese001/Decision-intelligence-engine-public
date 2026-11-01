"""Pinned BGE-M3 W4 delta retrieval on disposable graph/index generations."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import (
    ROOT, connect_sqlite, exact_file_copy, sha256_file, stable_id, write_json,
)
from dual_pipeline.document_pipeline_core.retrieval_visibility import source_retrieval_completion
from dual_pipeline.index_core.sidecar import build_sidecar
from dual_pipeline.release_core.manager import ReleaseManager
from dual_pipeline.release_core.helpers import _seal_receipt
from dual_pipeline.retrieval_release_core.evaluator import RetrievalReleaseEvaluator
from dual_pipeline.retrieval_suite_core.builder import AutomaticRetrievalReleaseSuiteBuilder
from tests.embedding_fixture import BgeM3TestBackend


def _insert(connection, table: str, row: dict) -> None:
    columns = list(row)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        [row[column] for column in columns],
    )


def _add_synthetic_source(graph: Path) -> tuple[str, str]:
    source_id = "source-w4-synthetic-table"
    assertion_id = "assertion-w4-synthetic-delta"
    source_version_id = "source-version-w4-synthetic-table"
    evidence_id = "evidence-w4-synthetic-table"
    text = ("W4 synthetic retrieval marker: the cobalt heron table assigns "
            "the Aurora migration owner to the engineering review team.")
    connection = connect_sqlite(graph)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        row = connection.execute(
            """SELECT a.* FROM assertion a
               JOIN assertion_evidence ae ON ae.assertion_id=a.assertion_id
               JOIN evidence_item ei ON ei.evidence_id=ae.evidence_id
               WHERE a.canonical_status='canonical'
                 AND a.validation_state='accepted_legacy'
                 AND a.currentness_state='current'
                 AND a.retrieval_eligible=1
                 AND a.classification='internal'
                 AND a.allowed_audiences_json='["internal_consulting"]'
                 AND ae.evidence_relationship='supports'
               ORDER BY a.assertion_id LIMIT 1"""
        ).fetchone()
        if row is None:
            raise AssertionError("isolated graph fixture lacks an eligible source assertion")
        original = dict(row)
        source = dict(connection.execute(
            "SELECT * FROM source WHERE source_id=?", (original["primary_source_id"],)
        ).fetchone())
        source.update(source_id=source_id, source_name="W4 synthetic table")
        _insert(connection, "source", source)
        old_evidence_id = connection.execute(
            "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? "
            "AND evidence_relationship='supports' ORDER BY evidence_id LIMIT 1",
            (original["assertion_id"],),
        ).fetchone()[0]
        old_evidence = dict(connection.execute(
            "SELECT * FROM evidence_item WHERE evidence_id=?", (old_evidence_id,)
        ).fetchone())
        version = dict(connection.execute(
            "SELECT * FROM source_version WHERE source_version_id=?",
            (old_evidence["source_version_id"],),
        ).fetchone())
        version.update(source_version_id=source_version_id, source_id=source_id,
                       source_fingerprint=hashlib.sha256(text.encode()).hexdigest(),
                       content_hash=hashlib.sha256(text.encode()).hexdigest(),
                       scope_text=text)
        _insert(connection, "source_version", version)
        old_evidence.update(
            evidence_id=evidence_id, evidence_fingerprint=hashlib.sha256(
                ("evidence:" + text).encode()).hexdigest(),
            source_id=source_id, source_version_id=source_version_id,
            locator_kind="row", locator_value="W4 synthetic table row 1",
            direct_quote=text,
        )
        _insert(connection, "evidence_item", old_evidence)
        original.update(
            assertion_id=assertion_id,
            assertion_fingerprint=hashlib.sha256(("assertion:" + text).encode()).hexdigest(),
            primary_source_id=source_id, extraction_run_id=None,
            origin_type="legacy_migration", scope_text=text,
            canonical_status="candidate", validation_state="needs_review",
            classification="internal", allowed_audiences_json='["internal_consulting"]',
            retrieval_eligible=1, canonical_eligible=0, hitl_required=0,
            approval_reference=None, qualifiers_json="{}",
            superseded_at=None, superseded_by_assertion_id=None,
        )
        _insert(connection, "assertion", original)
        _insert(connection, "assertion_evidence", {
            "assertion_id": assertion_id, "evidence_id": evidence_id,
            "evidence_relationship": "supports", "independent_corroboration": 0,
            "created_at": original["created_at"],
        })
        _insert(connection, "search_document", {
            "search_document_id": "search-w4-synthetic-table",
            "object_type": "assertion", "object_id": assertion_id,
            "title": "Cobalt heron Aurora migration owner",
            "body": text, "canonical_status": "candidate",
            "currentness_state": "current", "retrieval_eligible": 0,
            "source_id": source_id, "assertion_id": assertion_id,
            "updated_at": original["updated_at"],
        })
        connection.commit()
    finally:
        connection.close()
    return source_id, assertion_id


@unittest.skipUnless(os.environ.get("NEE_RUN_BGE_TESTS") == "1",
                     "Pinned real BGE-M3 profile is opt-in")
class Wave4RealBgeTests(unittest.TestCase):
    def test_new_source_delta_retrieves_with_acl_and_lane_denials(self):
        with tempfile.TemporaryDirectory(prefix="lens-w4-real-bge-") as directory:
            runtime = Path(directory) / "runtime"
            backend = BgeM3TestBackend()
            manager = ReleaseManager(runtime, ROOT / "graph" / "nee_graph.sqlite")
            active = manager.bootstrap(backend)
            generation_id = "generation-w4-synthetic-delta"
            generation = runtime / "generations" / generation_id
            generation.mkdir()
            graph = generation / "graph.sqlite"
            exact_file_copy(manager._resolve(active["graph_path"]), graph)
            source_id, assertion_id = _add_synthetic_source(graph)
            graph_hash = sha256_file(graph)
            snapshot = stable_id("graph-snapshot", graph_hash, length=24)
            sidecar = generation / "sidecar.sqlite"
            embedding = build_sidecar(
                graph_path=graph, sidecar_path=sidecar, generation_id=generation_id,
                graph_snapshot=snapshot, backend=backend,
                prior_sidecar_path=manager._resolve(active["sidecar_path"]),
            )
            candidate = {
                **active,
                "generation_id": generation_id,
                "graph_snapshot": snapshot,
                "graph_sha256": graph_hash,
                "sidecar_sha256": embedding["sidecar_sha256"],
                "graph_path": manager._relative(graph),
                "sidecar_path": manager._relative(sidecar),
                "previous_generation_id": active["generation_id"],
                "activated_at": None,
            }
            manager.validate_pair(candidate)
            receipt = _seal_receipt({
                "schema_version": "1.0.0",
                "receipt_id": "release-receipt-w4-synthetic-delta",
                "status": "PREPARED",
                "run_id": "run-w4-synthetic-delta",
                "trace_id": "a" * 32,
                "source_id": source_id,
                "approval_id": "fixture-only-approval",
                "change_set_id": "fixture-only-change",
                "change_set_digest": "b" * 64,
                "component_runs": [],
                "previous_pair": active,
                "next_pair": candidate,
                "graph_write_receipt": {},
                "graph_delta_summary": {},
                "label_quality_validation": {},
                "embedding_generation_receipt": embedding,
                "pair_validation": {},
                "production_qualified": False,
                "authorization": None,
                "prepared_at": "2026-10-06T00:00:00Z",
            })
            releases = runtime / "releases"
            releases.mkdir(exist_ok=True)
            receipt_path = releases / f"release-{generation_id}.json"
            write_json(receipt_path, receipt)
            suite = AutomaticRetrievalReleaseSuiteBuilder(
                runtime, ROOT / "graph" / "nee_graph.sqlite"
            ).build(receipt_path)
            evaluated = RetrievalReleaseEvaluator(
                runtime, ROOT / "graph" / "nee_graph.sqlite"
            ).evaluate(receipt_path, Path(suite["path"]), backend=backend)
            evaluation = evaluated["evaluation"]
            report = source_retrieval_completion(
                source_id=source_id, graph=graph, sidecar=sidecar,
                previous_sidecar=manager._resolve(active["sidecar_path"]),
                evaluation=evaluation, embedding_receipt=embedding,
            )
            self.assertEqual("PASS", evaluation["status"])
            self.assertEqual("VERIFIED", report["status"], report)
            self.assertEqual(1, report["eligible_assertion_count"])
            self.assertEqual(1, len(report["new_text_record_ids"]))
            self.assertGreaterEqual(embedding["delta_records"], 1)
            self.assertFalse(report["candidate_promoted"])
            self.assertEqual([assertion_id], [row["assertion_id"] for row in report["assertions"]])
            self.assertEqual(active["generation_id"], manager.active_pair()["generation_id"])
            target = os.environ.get("NEE_W4_PROFILE_RECEIPT")
            if target:
                result = {
                    "status": "PASS", "model_id": backend.model_id,
                    "model_revision": backend.model_revision,
                    "model_manifest_digest": backend.model_manifest_digest,
                    "evaluation_status": evaluation["status"],
                    "evaluation_digest": evaluation["canonical_digest"]["value"],
                    "suite_digest": suite["suite_digest"],
                    "source_id": source_id, "source_status": report["status"],
                    "new_text_record_ids": report["new_text_record_ids"],
                    "retrieved_record_ids": report["delta_retrieved_record_ids"],
                    "acl_denied_record_ids": report["acl_denied_record_ids"],
                    "lane_denied_record_ids": report["lane_denied_record_ids"],
                    "delta_vectors": embedding["delta_records"],
                    "active_generation_unchanged": True,
                    "isolated_graph_sha256": graph_hash,
                }
                result["receipt_digest"] = hashlib.sha256(json.dumps(
                    result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                with Path(target).open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
