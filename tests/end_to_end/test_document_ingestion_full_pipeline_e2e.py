"""End-to-end lifecycle tests for Stage 7 (Idempotent Graph Writer X3 & CoW Proof Gate)
and Stage 8 (Two-Phase Staged Publication, Coverage Verification, Retrieval Completion & Reconciliation).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.canonical_pipeline.publication_journal import guarded_publication
from dual_pipeline.canonical_pipeline.publication_verifier import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import connect_sqlite, sha256_file
from dual_pipeline.document_pipeline_core.retrieval_visibility import source_retrieval_completion
from dual_pipeline.graph_writer import GovernedGraphWriter
from tests.test_stage7_stage8_mutation_and_release_adversarial import (
    FULL_STAGE7_SCHEMA,
    build_op,
)
from tools.reconcile_ingestion_coverage import reconcile_coverage


class TestDocumentIngestionFullPipelineE2E(unittest.TestCase):
    """End-to-end verification of Stage 7 graph mutations + physical proof and Stage 8 publication lifecycle."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runtime_root / "canonical_graph.sqlite"
        self.sidecar_path = self.runtime_root / "retrieval_sidecar.sqlite"
        self._init_graph_and_sidecar()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init_graph_and_sidecar(self) -> None:
        conn = connect_sqlite(self.db_path)
        conn.executescript(FULL_STAGE7_SCHEMA)
        conn.execute(
            "INSERT INTO node_type_registry (node_type_id, canonical_name, identity_rule, registry_state) "
            "VALUES ('Workstream', 'Workstream', 'Deterministic ID; never auto-merge on name similarity.', 'active')"
        )
        conn.execute(
            "INSERT INTO node_type_registry (node_type_id, canonical_name, identity_rule, registry_state) "
            "VALUES ('Person', 'Person', 'Deterministic ID; never auto-merge on name similarity.', 'active')"
        )
        conn.execute(
            "INSERT INTO source VALUES ('doc-src-e2e', 'dual_pipeline_intake', 'Architecture Charter', '2026-10-15', 5, 'derivative_internal', NULL, 'Notes', '2026-10-15T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO source_version VALUES ('sv-e2e-1', 'doc-src-e2e', 1, 'fp-e2e', ?, '2026-10-15', 'day', 'exact', 5, 'scope', 'posture', '2026-10-15T00:00:00Z')",
            ("c" * 64,),
        )
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, source_version_id, locator_kind, locator_value, independent_corroboration, created_at) "
            "VALUES ('ev-e2e-1', 'doc-src-e2e', 'sv-e2e-1', 'section', 'p1', 0, '2026-10-15T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO governance_approval VALUES ('app-e2e-1', 'ref-e2e-1', "
            "json_object('allowed_actions', json_array('canonical_assertion_promotion')), 'approved', 1, '2026-10-15T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        with sqlite3.connect(self.sidecar_path) as sconn:
            sconn.executescript(
                "CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, retrieval_text_hash TEXT, artifact_json TEXT);"
                "CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY);"
            )

    def test_stage7_to_stage8_full_mutation_and_publication_lifecycle(self) -> None:
        """Executes Stage 7 governed graph mutation (entities + multi-object relations + FTS sync + 5 physical checks)
        followed by Stage 8 guarded publication, coverage verification, retrieval completion, and reconciliation."""
        writer = GovernedGraphWriter(self.db_path)

        payloads: dict[str, Any] = {
            "op-e1": {
                "disposition": "CANDIDATE",
                "entity_kind": "internal",
                "idempotent_upsert": True,
                "entity": {
                    "entity_id": "node-platform",
                    "node_type": "Workstream",
                    "canonical_name": "Unified Ingestion Platform",
                    "description": "Core ingestion platform.",
                    "status": "active",
                    "source_id": "doc-src-e2e",
                    "source_version_id": "sv-e2e-1",
                    "source_stable_identifier": "stable-platform",
                    "source_profile_digest": "c" * 64,
                    "evidence_ids": ["ev-e2e-1"],
                    "affected_candidate_ids": ["cand-ent-1"],
                    "possible_duplicate_labels": [],
                },
                "candidate": {
                    "candidate_id": "cand-ent-1",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "evidence_ids": ["ev-e2e-1"],
                    "entity_mentions": [{"reported_text": "Unified Ingestion Platform", "stable_identifier": "stable-platform"}],
                },
            },
            "op-e2": {
                "disposition": "CANDIDATE",
                "entity_kind": "internal",
                "idempotent_upsert": True,
                "entity": {
                    "entity_id": "node-lead-a",
                    "node_type": "Person",
                    "canonical_name": "Alice Vance",
                    "description": "Platform Architect.",
                    "status": "active",
                    "source_id": "doc-src-e2e",
                    "source_version_id": "sv-e2e-1",
                    "source_stable_identifier": "stable-lead-a",
                    "source_profile_digest": "c" * 64,
                    "evidence_ids": ["ev-e2e-1"],
                    "affected_candidate_ids": ["cand-ent-2"],
                    "possible_duplicate_labels": [],
                },
                "candidate": {
                    "candidate_id": "cand-ent-2",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "evidence_ids": ["ev-e2e-1"],
                    "entity_mentions": [{"reported_text": "Alice Vance", "stable_identifier": "stable-lead-a"}],
                },
            },
            "op-e3": {
                "disposition": "CANDIDATE",
                "entity_kind": "internal",
                "idempotent_upsert": True,
                "entity": {
                    "entity_id": "node-lead-b",
                    "node_type": "Person",
                    "canonical_name": "Bob Sterling",
                    "description": "Co-Architect.",
                    "status": "active",
                    "source_id": "doc-src-e2e",
                    "source_version_id": "sv-e2e-1",
                    "source_stable_identifier": "stable-lead-b",
                    "source_profile_digest": "c" * 64,
                    "evidence_ids": ["ev-e2e-1"],
                    "affected_candidate_ids": ["cand-ent-3"],
                    "possible_duplicate_labels": [],
                },
                "candidate": {
                    "candidate_id": "cand-ent-3",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "evidence_ids": ["ev-e2e-1"],
                    "entity_mentions": [{"reported_text": "Bob Sterling", "stable_identifier": "stable-lead-b"}],
                },
            },
            "op-a1": {
                "disposition": "APPROVED",
                "subject_node_id": "node-platform",
                "predicate": "HAS_ARCHITECT",
                "object_node_id": "node-lead-a",
                "retrieval_audiences": ["internal_consulting"],
                "candidate": {
                    "candidate_id": "cand-e2e-1",
                    "candidate_type": "assertion",
                    "normalized_text": "Unified Ingestion Platform has architect Alice Vance.",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "extraction_confidence": 0.94,
                    "materiality": "high",
                    "evidence_ids": ["ev-e2e-1"],
                },
            },
            "op-a2": {
                "disposition": "APPROVED",
                "subject_node_id": "node-platform",
                "predicate": "HAS_ARCHITECT",
                "object_node_id": "node-lead-b",
                "retrieval_audiences": ["internal_consulting"],
                "candidate": {
                    "candidate_id": "cand-e2e-2",
                    "candidate_type": "assertion",
                    "normalized_text": "Unified Ingestion Platform has architect Bob Sterling.",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "extraction_confidence": 0.93,
                    "materiality": "high",
                    "evidence_ids": ["ev-e2e-1"],
                },
            },
        }
        operations = [
            build_op("op-e1", 1, "create_entity", "entity", "node-platform", "cand-ent-1", ["ev-e2e-1"], payloads["op-e1"]),
            build_op("op-e2", 2, "create_entity", "entity", "node-lead-a", "cand-ent-2", ["ev-e2e-1"], payloads["op-e2"]),
            build_op("op-e3", 3, "create_entity", "entity", "node-lead-b", "cand-ent-3", ["ev-e2e-1"], payloads["op-e3"]),
            build_op("op-a1", 4, "add_approved_assertion", "assertion", "assert-e2e-1", "cand-e2e-1", ["ev-e2e-1"], payloads["op-a1"]),
            build_op("op-a2", 5, "add_approved_assertion", "assertion", "assert-e2e-2", "cand-e2e-2", ["ev-e2e-1"], payloads["op-a2"]),
        ]

        change_set = seal_artifact(
            create_artifact(
                "graph-change-set",
                "cs-e2e-1",
                change_set_id="cs-e2e-1",
                run_id="run-e2e-1",
                trace_id="2" * 32,
                status="approved",
                approval_id="app-e2e-1",
                base_graph_snapshot="snap-e2e",
                base_graph_hash=sha256_file(self.db_path),
                operations=operations,
                transaction_required=True,
                rollback_required=True,
                extensions={
                    "com.partner_advisory.nee.source": {
                        "source_id": "doc-src-e2e",
                        "source_version_id": "sv-e2e-1",
                        "name": "Architecture Charter",
                        "package_digest": "c" * 64,
                    },
                    "com.partner_advisory.nee.operation-payloads": payloads,
                },
            )
        )

        def _publish_callable() -> dict[str, Any]:
            apply_res = writer.apply(change_set)
            self.assertEqual(apply_res["operation_count"], 5)
            self.assertIn("physical_proof", apply_res)
            self.assertEqual(apply_res["physical_proof"]["overall"], "PASS")
            return {
                "status": "RELEASE_ACTIVATED",
                "release_id": "rel-e2e-1",
                "generation_id": "gen-e2e-1",
                "active_graph_path": str(self.db_path),
                "active_sidecar_path": str(self.sidecar_path),
                "apply_result": apply_res,
            }

        pub_res = guarded_publication(
            runtime_root=self.runtime_root,
            directive_id="dir-e2e-1",
            bindings_factory=lambda: {"source_id": "doc-src-e2e", "package_digest": "d" * 64},
            publish=_publish_callable,
        )
        self.assertEqual(pub_res["status"], "RELEASE_ACTIVATED")

        # Verify both multi-object relational assertions remain current in SQLite and indexed in search_fts
        conn = connect_sqlite(self.db_path)
        current_rows = conn.execute(
            "SELECT assertion_id, currentness_state FROM assertion WHERE subject_node_id='node-platform' AND predicate='HAS_ARCHITECT' ORDER BY assertion_id"
        ).fetchall()
        self.assertEqual(
            [(r["assertion_id"], r["currentness_state"]) for r in current_rows],
            [("assert-e2e-1", "current"), ("assert-e2e-2", "current")],
        )
        fts_rows = conn.execute("SELECT COUNT(*) AS c FROM search_fts").fetchone()["c"]
        self.assertEqual(fts_rows, 2)
        conn.close()

        # Stage 8: Verify fact coverage in graph (with schema validation)
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-e2e-1",
            run_id="run-e2e-1",
            source_digest="c" * 64,
            items=[
                EvidenceItem(
                    item_id="item-e2e-1",
                    item_type=ItemType.SPEAKER_ROLE.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.PROPOSED.value,
                    source_origin={"origin_type": "DOCUMENT", "source_id": "doc-src-e2e"},
                    disposition_reason="Proposed architect relation",
                    proposed_fact={
                        "subject": "Unified Ingestion Platform",
                        "predicate": "HAS_ARCHITECT",
                        "object": "Unified Ingestion Platform has architect Alice Vance.",
                    },
                )
            ],
        )
        cov_ok, cov_violations, cov_receipt = verify_fact_coverage_in_graph(
            graph_db=self.db_path,
            manifest=manifest,
            proposal_digest="d" * 64,
            activated_generation_id="gen-e2e-1",
            output_dir=self.runtime_root,
        )
        self.assertTrue(cov_ok, f"Coverage violations: {cov_violations}")
        self.assertEqual(cov_receipt["status"], "VERIFIED")

        # Populate sidecar records for source_retrieval_completion verification
        with sqlite3.connect(self.sidecar_path) as sconn:
            for rec_id, aid in [("rec-1", "assert-e2e-1"), ("rec-2", "assert-e2e-2")]:
                art = {"extensions": {"com.partner_advisory.nee.source-id": "doc-src-e2e", "com.partner_advisory.nee.graph-object-id": aid}}
                sconn.execute("INSERT INTO retrieval_record VALUES (?, 'approved', 'hash-new', ?)", (rec_id, json.dumps(art)))
                sconn.execute("INSERT INTO dense_vector VALUES (?)", (rec_id,))

        eval_report = {
            "status": "PASS",
            "coverage": {"added_or_changed_record_ids": ["rec-1", "rec-2"]},
            "case_results": [
                {"scope": "delta", "query_kind": "semantic_paraphrase", "expected_record_ids": ["rec-1", "rec-2"], "forbidden_record_ids": [], "candidate_retrieved_record_ids": ["rec-1", "rec-2"]},
                {"scope": "delta", "query_kind": "acl_boundary", "expected_record_ids": [], "forbidden_record_ids": ["rec-1", "rec-2"], "candidate_retrieved_record_ids": []},
                {"scope": "delta", "query_kind": "lane_isolation", "expected_record_ids": [], "forbidden_record_ids": ["rec-1", "rec-2"], "candidate_retrieved_record_ids": []},
            ],
        }
        ret_comp = source_retrieval_completion(
            source_id="doc-src-e2e",
            graph=self.db_path,
            sidecar=self.sidecar_path,
            previous_sidecar=None,
            evaluation=eval_report,
            embedding_receipt={"delta_records": 2},
        )
        self.assertEqual(ret_comp["status"], "VERIFIED")

        # Verify reconcile_coverage reports COMPLETE when active_pair and receipt are written
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-e2e-1", "graph_path": str(self.db_path), "sidecar_path": str(self.sidecar_path)}),
            encoding="utf-8",
        )
        sources_dir = self.root / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        src_file = sources_dir / "doc-src-e2e.docx"
        src_file.write_text("dummy docx content", encoding="utf-8")
        receipts_dir = self.runtime_root / "receipts" / "doc-src-e2e"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        (receipts_dir / "execution_receipt.json").write_text(
            json.dumps(
                {
                    "source_path": str(src_file),
                    "terminal_outcome": "PASS",
                    "publication_coverage_verified": True,
                    "delta_table_counts": {"assertion": 2},
                }
            ),
            encoding="utf-8",
        )
        recon = reconcile_coverage(sources=[src_file], runtime_root=self.runtime_root)
        self.assertEqual(recon["counts_by_status"]["COVERED"], 1)


if __name__ == "__main__":
    unittest.main()
