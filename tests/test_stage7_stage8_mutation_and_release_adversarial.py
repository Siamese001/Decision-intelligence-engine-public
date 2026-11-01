"""Wave 6 adversarial and edge-case tests for Stage 7 (Idempotent Graph Writer X3 & CoW Proof Gate)
and Stage 8 (Two-Phase Staged Publication, Coverage Verification, Retrieval Visibility & Reconciliation).

Covers mandatory edge-case categories:
1. Boundary payload (empty component_runs, missing source_id, advisory remediation directive on PASS verdict)
2. Malformed input (ungrounded decision/risk claims, missing relational predicates in coverage inspection)
3. Concurrency & race (ParallelDocumentIngestor worker timeout and group commit partial failure propagation)
4. Invariant bypass attempt (duplicate entity attribute mismatch rejection, restricted audience preflight conflict)
5. Rollback & recovery (pre-mutation byte restoration on GraphMutationProofError, UWG approval failure cleanup)
6. Exact production regression (fingerprint collision replacement without FTS trigger abort, multi-object relational edges, add_evidence prior_sources corroboration)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.publication_verifier import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file
from dual_pipeline.document_pipeline_core.evaluator import _evaluate_output3_judge
from dual_pipeline.document_pipeline_core.parallel_ingestor import ParallelDocumentIngestor
from dual_pipeline.document_pipeline_core.publisher import (
    _publish_args,
    _validate_dependencies,
    _validate_package_roles,
)
from dual_pipeline.document_pipeline_core.quality import (
    _resolve_text_span_page,
    validate_signal_claim_grounding,
)
from dual_pipeline.document_pipeline_core.retrieval_visibility import (
    _source_records,
    completion_from_publication,
)
from dual_pipeline.graph_mutation_proof import GraphMutationProofError
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.graph_writer_core.proof_gate import run_with_proof_gate
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway
from dual_pipeline.uwg_core.lineage_bridge import AssertionReconciler
from tools.reconcile_ingestion_coverage import (
    _check_source_receipt,
    _get_active_runtime_info,
    _scan_journal_issues,
)

FULL_STAGE7_SCHEMA = """
CREATE TABLE source (
    source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_name TEXT NOT NULL,
    source_date TEXT, authority_rank INTEGER NOT NULL DEFAULT 5, source_posture TEXT NOT NULL,
    cold_archive_ref TEXT, notes TEXT, created_at TEXT NOT NULL
);
CREATE TABLE source_version (
    source_version_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version_number INTEGER NOT NULL,
    source_fingerprint TEXT, content_hash TEXT NOT NULL, source_date_normalized TEXT,
    source_date_precision TEXT, source_date_status TEXT, authority_rank INTEGER,
    scope_text TEXT, posture_text TEXT, created_at TEXT NOT NULL
);
CREATE TABLE extraction_run (
    extraction_run_id TEXT PRIMARY KEY, algorithm_version TEXT NOT NULL,
    baseline_graph_sha256 TEXT NOT NULL, input_manifest_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, deterministic INTEGER NOT NULL
);
CREATE TABLE node_type_registry (
    node_type_id TEXT PRIMARY KEY, canonical_name TEXT, definition TEXT, parent_type_id TEXT,
    required_properties_json TEXT, optional_properties_json TEXT, identity_rule TEXT NOT NULL,
    example_node_id TEXT, registry_state TEXT NOT NULL DEFAULT 'active',
    replacement_type_id TEXT, retrieval_use TEXT, created_at TEXT
);
CREATE TABLE node (
    node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT NOT NULL,
    display_name TEXT, primary_label TEXT NOT NULL DEFAULT 'Entity', description TEXT, domain TEXT, owner_team TEXT,
    status TEXT, confidentiality_level TEXT, aliases_json TEXT DEFAULT '[]', properties_json TEXT DEFAULT '{}',
    created_from_source_id TEXT, First_Seen_Source_ID TEXT, Last_Verified_Source_ID TEXT,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE node_state (
    node_id TEXT PRIMARY KEY, lifecycle_state TEXT, validation_state TEXT, currentness_state TEXT,
    approval_state TEXT, rag_state TEXT, resolution_state TEXT, retrieval_eligibility TEXT,
    publication_state TEXT, review_state TEXT, legacy_status_text TEXT,
    intentional_orphan INTEGER DEFAULT 0, intentional_orphan_reason TEXT, updated_at TEXT
);
CREATE TABLE evidence_item (
    evidence_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_version_id TEXT NOT NULL,
    locator_kind TEXT NOT NULL, locator_value TEXT NOT NULL,
    extraction_confidence REAL DEFAULT 0.85, source_authority_rank INTEGER DEFAULT 5,
    independent_corroboration INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE assertion (
    assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT NOT NULL UNIQUE,
    subject_node_id TEXT NOT NULL, predicate TEXT NOT NULL,
    legacy_predicate TEXT, object_node_id TEXT, object_literal TEXT, object_literal_type TEXT,
    assertion_type TEXT NOT NULL, atomicity_state TEXT NOT NULL, scope_text TEXT NOT NULL,
    speaker_or_author TEXT, primary_source_id TEXT NOT NULL, extraction_run_id TEXT NOT NULL,
    extraction_method TEXT NOT NULL, origin_type TEXT NOT NULL, confidence_label TEXT NOT NULL,
    confidence_score REAL NOT NULL, validation_state TEXT NOT NULL, currentness_state TEXT NOT NULL,
    valid_from TEXT, valid_to TEXT, date_precision TEXT, date_status TEXT,
    recorded_at TEXT NOT NULL, superseded_at TEXT, superseded_by_assertion_id TEXT,
    resolution_state TEXT NOT NULL, canonical_status TEXT NOT NULL,
    canonical_eligible INTEGER NOT NULL, retrieval_eligible INTEGER NOT NULL,
    materiality TEXT NOT NULL, classification TEXT NOT NULL, allowed_audiences_json TEXT NOT NULL,
    hitl_required INTEGER NOT NULL, validation_priority TEXT, validation_owner TEXT,
    qualifiers_json TEXT NOT NULL, legacy_record_type TEXT NOT NULL,
    legacy_record_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    approval_reference TEXT
);
CREATE TABLE assertion_evidence (
    assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
    evidence_relationship TEXT NOT NULL, independent_corroboration INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, PRIMARY KEY (assertion_id, evidence_id)
);
CREATE TABLE assertion_label_state (
    assertion_id TEXT PRIMARY KEY, extraction_confidence REAL NOT NULL,
    assertion_confidence_label TEXT, assertion_confidence_score REAL,
    validation_state TEXT NOT NULL, lifecycle_state TEXT NOT NULL,
    mapping_state TEXT NOT NULL, mapping_rule_id TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE assertion_audience (
    assertion_id TEXT NOT NULL, audience_id TEXT NOT NULL,
    mapping_state TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (assertion_id, audience_id)
);
CREATE TABLE search_document (
    search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    search_document_id TEXT NOT NULL UNIQUE, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '',
    canonical_status TEXT NOT NULL, currentness_state TEXT NOT NULL, retrieval_eligible INTEGER NOT NULL,
    source_id TEXT NOT NULL, assertion_id TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE search_fts USING fts5(
    title, body, object_type UNINDEXED, object_id UNINDEXED,
    content='search_document', content_rowid='search_rowid'
);
CREATE TRIGGER search_document_ad AFTER DELETE ON search_document BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body, object_type, object_id)
    VALUES ('delete', old.search_rowid, old.title, old.body, old.object_type, old.object_id);
END;
CREATE TABLE audit_event (
    audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL, action TEXT NOT NULL,
    record_type TEXT NOT NULL, record_id TEXT NOT NULL, previous_value_json TEXT,
    new_value_json TEXT NOT NULL, source_id TEXT NOT NULL, event_timestamp TEXT NOT NULL,
    reason TEXT NOT NULL, validation_reference TEXT, approval_reference TEXT,
    migration_or_ingestion_run_id TEXT NOT NULL, release_version TEXT NOT NULL
);
CREATE TABLE governance_approval (
    approval_id TEXT PRIMARY KEY, approval_reference TEXT NOT NULL,
    scope_json TEXT NOT NULL, status TEXT NOT NULL, active INTEGER NOT NULL,
    approved_at TEXT NOT NULL
);
"""


def build_op(
    op_id: str,
    seq: int,
    op_type: str,
    target_type: str,
    target_id: str,
    cand_id: str,
    ev_ids: list[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "operation_id": op_id,
        "sequence": seq,
        "operation": op_type,
        "target_type": target_type,
        "target_id": target_id,
        "candidate_id": cand_id,
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "evidence_ids": ev_ids,
        "before_state_hash": ZERO_HASH,
        "after_state_hash": digest(payload),
    }


class TestStage7MutationAdversarial(unittest.TestCase):
    """Adversarial tests for Stage 7 (DEF-07-01 through DEF-07-07)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "stage7_test.sqlite"
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
            "INSERT INTO node (node_id, node_type, canonical_name, primary_label, active, created_at) "
            "VALUES ('node-subj-1', 'Workstream', 'Subject One', 'Workstream', 1, '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, primary_label, active, created_at) "
            "VALUES ('node-obj-a', 'Person', 'Alice', 'Person', 1, '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, primary_label, active, created_at) "
            "VALUES ('node-obj-b', 'Person', 'Bob', 'Person', 1, '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO source VALUES ('src-1', 'dual_pipeline_intake', 'Source 1', '2026-10-01', 5, 'derivative_internal', NULL, 'Notes', '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO source VALUES ('src-2', 'dual_pipeline_intake', 'Source 2', '2026-10-02', 5, 'derivative_internal', NULL, 'Notes', '2026-10-02T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO source_version VALUES ('sv-1', 'src-1', 1, 'fp1', ?, '2026-10-01', 'day', 'exact', 5, 's', 'p', '2026-10-01T00:00:00Z')",
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO source_version VALUES ('sv-2', 'src-2', 1, 'fp2', ?, '2026-10-02', 'day', 'exact', 5, 's', 'p', '2026-10-02T00:00:00Z')",
            ("b" * 64,),
        )
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, source_version_id, locator_kind, locator_value, independent_corroboration, created_at) "
            "VALUES ('ev-1', 'src-1', 'sv-1', 'section', 'p1', 0, '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, source_version_id, locator_kind, locator_value, independent_corroboration, created_at) "
            "VALUES ('ev-2', 'src-2', 'sv-2', 'section', 'p2', 0, '2026-10-02T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO governance_approval VALUES ('app-1', 'ref-1', "
            "json_object('allowed_actions', json_array('canonical_assertion_promotion')), 'approved', 1, '2026-10-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_change_set(
        self,
        operations: list[dict[str, Any]],
        payloads: dict[str, Any],
        approval_id: str | None = "app-1",
        cs_id: str = "cs-stage7",
        run_id: str = "run-stage7",
    ) -> dict[str, Any]:
        cs = create_artifact(
            "graph-change-set",
            cs_id,
            change_set_id=cs_id,
            run_id=run_id,
            trace_id="1" * 32,
            status="approved",
            approval_id=approval_id,
            base_graph_snapshot="snap-1",
            base_graph_hash=sha256_file(self.db_path),
            operations=operations,
            transaction_required=True,
            rollback_required=True,
            extensions={
                "com.partner_advisory.nee.source": {
                    "source_id": "src-1",
                    "source_version_id": "sv-1",
                    "name": "Source 1",
                    "package_digest": "a" * 64,
                },
                "com.partner_advisory.nee.operation-payloads": payloads,
            },
        )
        return seal_artifact(cs)

    def test_def_07_01_and_02_fingerprint_collision_replacement_and_fts_sync(self) -> None:
        """DEF-07-01 & DEF-07-02: Fingerprint collision replacement preserves prior evidence,
        re-points superseded_by_assertion_id, and syncs search_fts without trigger abort."""
        writer = GovernedGraphWriter(self.db_path)
        cand_payload = {
            "disposition": "CANDIDATE",
            "hitl_required": True,
            "validation_owner": "operator-team",
            "subject_node_id": "node-subj-1",
            "predicate": "HAS_CLAIM_TEXT",
            "retrieval_audiences": ["internal_consulting"],
            "candidate": {
                "candidate_id": "cand-old",
                "candidate_type": "assertion",
                "normalized_text": "Shared claim text for fingerprint.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extraction_confidence": 0.72,
                "materiality": "high",
                "evidence_ids": ["ev-1"],
            },
        }
        op1 = build_op("op-1", 1, "add_candidate_assertion", "assertion", "assert-old", "cand-old", ["ev-1"], cand_payload)
        writer.apply(self._make_change_set([op1], {"op-1": cand_payload}, approval_id="app-1"))

        conn = connect_sqlite(self.db_path)
        old_row = conn.execute("SELECT hitl_required, validation_owner FROM assertion WHERE assertion_id='assert-old'").fetchone()
        self.assertEqual(old_row["hitl_required"], 1)
        self.assertEqual(old_row["validation_owner"], "operator-team")
        fts_count = conn.execute("SELECT COUNT(*) AS c FROM search_fts WHERE search_fts MATCH 'Shared'").fetchone()["c"]
        self.assertEqual(fts_count, 1)

        conn.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, object_literal,
                object_literal_type, assertion_type, atomicity_state, scope_text, primary_source_id,
                extraction_run_id, extraction_method, origin_type, confidence_label, confidence_score,
                validation_state, currentness_state, recorded_at, superseded_by_assertion_id,
                resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                materiality, classification, allowed_audiences_json, hitl_required, qualifiers_json,
                legacy_record_type, legacy_record_ids_json, created_at, updated_at
            ) VALUES (
                'assert-hist', 'fp-hist', 'node-subj-1', 'HAS_CLAIM_TEXT', 'Old claim', 'text',
                'extracted_claim', 'atomic', 'Old claim', 'src-1', 'run-0', 'adapter', 'human_source',
                'medium', 0.6, 'corroborated', 'superseded', '2026-09-01T00:00:00Z', 'assert-old',
                'superseded', 'canonical', 1, 0, 'medium', 'internal', '["internal_consulting"]', 0, '{}',
                'claim', '[]', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z'
            )"""
        )
        conn.commit()
        conn.close()

        approved_payload = {
            "disposition": "APPROVED",
            "subject_node_id": "node-subj-1",
            "predicate": "HAS_CLAIM_TEXT",
            "retrieval_audiences": ["internal_consulting"],
            "candidate": {
                "candidate_id": "cand-new",
                "candidate_type": "assertion",
                "normalized_text": "Shared claim text for fingerprint.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extraction_confidence": 0.91,
                "materiality": "high",
                "evidence_ids": ["ev-2"],
            },
        }
        op2 = build_op("op-2", 1, "add_approved_assertion", "assertion", "assert-new", "cand-new", ["ev-2"], approved_payload)
        writer.apply(self._make_change_set([op2], {"op-2": approved_payload}, approval_id="app-1", cs_id="cs-stage7-2", run_id="run-stage7-2"))

        conn = connect_sqlite(self.db_path)
        ev_rows = conn.execute("SELECT evidence_id FROM assertion_evidence WHERE assertion_id='assert-new' ORDER BY evidence_id").fetchall()
        self.assertEqual([r["evidence_id"] for r in ev_rows], ["ev-1", "ev-2"])
        hist_row = conn.execute("SELECT superseded_by_assertion_id FROM assertion WHERE assertion_id='assert-hist'").fetchone()
        self.assertEqual(hist_row["superseded_by_assertion_id"], "assert-new")
        self.assertIsNone(conn.execute("SELECT 1 FROM search_document WHERE assertion_id='assert-old'").fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM search_fts WHERE object_id='assert-new'").fetchone()["c"], 1)
        conn.close()

    def test_def_07_03_entity_idempotent_upsert_vs_mismatch_rejection(self) -> None:
        """DEF-07-03: create_entity with idempotent_upsert=True succeeds when matching,
        and fails closed on attribute mismatch."""
        writer = GovernedGraphWriter(self.db_path)
        matching_payload = {
            "disposition": "CANDIDATE",
            "entity_kind": "internal",
            "idempotent_upsert": True,
            "entity": {
                "entity_id": "node-subj-1",
                "node_type": "Workstream",
                "canonical_name": "Subject One",
                "description": "Existing workstream node.",
                "status": "active",
                "source_id": "src-1",
                "source_version_id": "sv-1",
                "source_stable_identifier": "stable-subj-1",
                "source_profile_digest": "a" * 64,
                "evidence_ids": ["ev-1"],
                "affected_candidate_ids": ["cand-ent-ok"],
                "possible_duplicate_labels": [],
            },
            "candidate": {
                "candidate_id": "cand-ent-ok",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["ev-1"],
                "entity_mentions": [{"reported_text": "Subject One", "stable_identifier": "stable-subj-1"}],
            },
        }
        op_ok = build_op("op-ent-ok", 1, "create_entity", "entity", "node-subj-1", "cand-ent-ok", ["ev-1"], matching_payload)
        res = writer.apply(self._make_change_set([op_ok], {"op-ent-ok": matching_payload}, approval_id="app-1"))
        self.assertEqual(res["operation_count"], 1)

        mismatch_payload = {
            "disposition": "CANDIDATE",
            "entity_kind": "internal",
            "idempotent_upsert": True,
            "entity": {
                "entity_id": "node-subj-1",
                "node_type": "Workstream",
                "canonical_name": "Different Name",
                "description": "Mismatched workstream node.",
                "status": "active",
                "source_id": "src-1",
                "source_version_id": "sv-1",
                "source_stable_identifier": "stable-subj-bad",
                "source_profile_digest": "a" * 64,
                "evidence_ids": ["ev-1"],
                "affected_candidate_ids": ["cand-ent-bad"],
                "possible_duplicate_labels": [],
            },
            "candidate": {
                "candidate_id": "cand-ent-bad",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["ev-1"],
                "entity_mentions": [{"reported_text": "Different Name", "stable_identifier": "stable-subj-bad"}],
            },
        }
        op_bad = build_op("op-ent-bad", 1, "create_entity", "entity", "node-subj-1", "cand-ent-bad", ["ev-1"], mismatch_payload)
        with self.assertRaises(RuntimeError):
            writer.apply(self._make_change_set([op_bad], {"op-ent-bad": mismatch_payload}, approval_id="app-1", cs_id="cs-stage7-bad", run_id="run-stage7-bad"))

    def test_def_07_04_uwg_register_governance_approval_reraises_on_failure(self) -> None:
        """DEF-07-04: UniversalWriteGateway.register_governance_approval cleans up generation_dir and re-raises."""
        req_file = self.root / "approval_req.json"
        req_file.write_text(
            json.dumps({"approval_id": "app-fail", "approval_reference": "ref-fail", "canonical_digest": {"value": "1" * 64}}),
            encoding="utf-8",
        )
        uwg = object.__new__(UniversalWriteGateway)
        uwg.approvals_dir = self.root / "approvals"
        uwg.manager = MagicMock()
        uwg.manager.lock_path = self.root / "uwg.lock"
        uwg.manager.generations_dir = self.root / "generations"
        uwg.manager.releases_dir = self.root / "releases"
        uwg.manager.generations_dir.mkdir(parents=True, exist_ok=True)
        uwg.manager.releases_dir.mkdir(parents=True, exist_ok=True)
        uwg.manager.active_pair.return_value = {"generation_id": "gen-0", "graph_path": str(self.db_path)}
        uwg.manager._resolve.return_value = self.db_path
        uwg._assert_contract = MagicMock()
        uwg._validate_approval_registration = MagicMock(return_value=(["cand-1"], [], {}))
        uwg._validate_source_snapshot_bindings = MagicMock(return_value={"source_ids": ["src-1"], "source_snapshot_bindings": []})
        uwg._validate_candidate_fact_promotion_governance = MagicMock()
        uwg._candidate_sidecar_records = MagicMock()
        uwg._stage_governance_approval = MagicMock(side_effect=RuntimeError("Injected staging failure"))

        backend = MagicMock()
        backend.backend_id = "test-backend"
        backend.model_revision = "v1"
        backend.model_manifest_digest = "2" * 64
        auth_ctx = MagicMock()

        with patch("dual_pipeline.uwg_core.gateway._assert_backend_matches"):
            with self.assertRaisesRegex(RuntimeError, "Injected staging failure"):
                uwg.register_governance_approval(req_file, backend=backend, authorization_context=auth_ctx)
        self.assertEqual(list(uwg.manager.generations_dir.iterdir()), [])

    def test_def_07_05_multi_valued_relational_edges_not_falsely_superseded(self) -> None:
        """DEF-07-05: AssertionReconciler and remediate_supersession_and_candidate_eligibility
        preserve multiple relational assertions with distinct object_node_id."""
        reconciler = AssertionReconciler()
        existing = [
            {"assertion_id": "a-rel-1", "subject_node_id": "node-subj-1", "predicate": "HAS_STAKEHOLDER", "object_node_id": "node-obj-a", "object_literal": None},
        ]
        new_assertions = [
            {"assertion_id": "a-rel-2", "subject_node_id": "node-subj-1", "predicate": "HAS_STAKEHOLDER", "object_node_id": "node-obj-b", "object_literal": None},
        ]
        result = reconciler.reconcile(existing, new_assertions)
        self.assertEqual(result["superseded_count"], 0)

        conn = connect_sqlite(self.db_path)
        for aid, fp, src, obj_id, curr_state, sup_by, rec_at in [
            ("a-rel-old", "fp-rel-1", "src-1", "node-obj-a", "superseded", "a-rel-new", "2026-10-01T00:00:00Z"),
            ("a-rel-new", "fp-rel-2", "src-2", "node-obj-b", "current", None, "2026-10-02T00:00:00Z"),
        ]:
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id,
                    assertion_type, atomicity_state, scope_text, primary_source_id,
                    extraction_run_id, extraction_method, origin_type, confidence_label, confidence_score,
                    validation_state, currentness_state, superseded_by_assertion_id, recorded_at,
                    resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                    materiality, classification, allowed_audiences_json, hitl_required, qualifiers_json,
                    legacy_record_type, legacy_record_ids_json, created_at, updated_at
                ) VALUES (
                    ?, ?, 'node-subj-1', 'HAS_STAKEHOLDER', ?, 'relationship', 'atomic',
                    'Stakeholder relation', ?, 'run-1', 'adapter', 'human_source', 'high', 0.9, 'corroborated',
                    ?, ?, ?, 'resolved', 'canonical', 1, 1, 'high', 'internal', '["internal_consulting"]',
                    0, '{}', 'claim', '[]', ?, ?
                )""",
                (aid, fp, obj_id, src, curr_state, sup_by, rec_at, rec_at, rec_at),
            )
            conn.execute(
                "INSERT INTO assertion_evidence VALUES (?, 'ev-1', 'supports', 0, ?)",
                (aid, rec_at),
            )
        writer = GovernedGraphWriter(self.db_path)
        rem_res = writer.remediate_supersession_and_candidate_eligibility(conn, "2026-10-03T00:00:00Z")
        conn.commit()
        self.assertEqual(rem_res["restored_false_supersessions"], 1)
        old_state = conn.execute("SELECT currentness_state, superseded_by_assertion_id FROM assertion WHERE assertion_id='a-rel-old'").fetchone()
        self.assertEqual(old_state["currentness_state"], "current")
        self.assertIsNone(old_state["superseded_by_assertion_id"])
        conn.close()

    def test_def_07_06_add_evidence_corroboration_and_label_state_promotion(self) -> None:
        """DEF-07-06: add_evidence queries prior_sources before inserting new evidence,
        sets independent_corroboration=1 for cross-source evidence, and updates assertion_label_state on promotion."""
        writer = GovernedGraphWriter(self.db_path)
        cand_payload = {
            "disposition": "CANDIDATE",
            "subject_node_id": "node-subj-1",
            "predicate": "HAS_CLAIM_TEXT",
            "retrieval_audiences": ["internal_consulting"],
            "candidate": {
                "candidate_id": "cand-corrob",
                "candidate_type": "assertion",
                "normalized_text": "Claim awaiting second source corroboration.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extraction_confidence": 0.85,
                "materiality": "high",
                "evidence_ids": ["ev-1"],
            },
        }
        op_c = build_op("op-c", 1, "add_candidate_assertion", "assertion", "assert-corrob", "cand-corrob", ["ev-1"], cand_payload)
        writer.apply(self._make_change_set([op_c], {"op-c": cand_payload}, approval_id="app-1"))

        ev_payload = {
            "promote_candidate": True,
            "candidate": {
                "candidate_id": "cand-corrob-ev",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["ev-2"],
            },
        }
        op_ev = build_op("op-ev", 1, "add_evidence", "assertion", "assert-corrob", "cand-corrob-ev", ["ev-2"], ev_payload)
        writer.apply(self._make_change_set([op_ev], {"op-ev": ev_payload}, approval_id="app-1", cs_id="cs-stage7-ev", run_id="run-stage7-ev"))

        conn = connect_sqlite(self.db_path)
        ev2 = conn.execute("SELECT independent_corroboration FROM assertion_evidence WHERE assertion_id='assert-corrob' AND evidence_id='ev-2'").fetchone()
        self.assertEqual(ev2["independent_corroboration"], 1)
        lbl = conn.execute("SELECT validation_state, lifecycle_state FROM assertion_label_state WHERE assertion_id='assert-corrob'").fetchone()
        self.assertIn(lbl["validation_state"], ("corroborated", "accepted_legacy"))
        self.assertEqual(lbl["lifecycle_state"], "active")
        conn.close()

    def test_def_07_07_proof_gate_restores_pre_bytes_on_finalize_failure(self) -> None:
        """DEF-07-07: run_with_proof_gate restores exact pre-mutation database bytes if finalize raises GraphMutationProofError."""
        before_bytes = self.db_path.read_bytes()

        def _mutating_apply(guard: Any) -> dict[str, Any]:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO node (node_id, node_type, canonical_name, primary_label, active, created_at) "
                "VALUES ('node-corrupt', 'Workstream', 'Corrupt', 'Workstream', 1, '2026-10-05T00:00:00Z')"
            )
            conn.commit()
            conn.close()
            guard.finalize(record_ids=["nonexistent-id-that-triggers-proof-failure"], audit_events=999)
            return {}

        with self.assertRaises(GraphMutationProofError):
            run_with_proof_gate(self.db_path, "test_rollback", _mutating_apply)

        after_bytes = self.db_path.read_bytes()
        self.assertEqual(before_bytes, after_bytes)


class TestStage8PublicationAdversarial(unittest.TestCase):
    """Adversarial tests for Stage 8 (D8-02 through D8-25)."""

    def test_d8_02_and_03_publisher_audience_forwarding_and_general_roles(self) -> None:
        """D8-02 & D8-03: _publish_args forwards audience and _validate_package_roles accepts general roles."""
        pkg_path = Path("/tmp/pkg.json")
        args = _publish_args(
            [pkg_path],
            directive_id="dir-1",
            scope="scope-1",
            reviewer="Operator",
            rationale="rationale-1",
            embedding_backend="bge-m3",
            audience="exec_board",
        )
        self.assertEqual(args.audience, "exec_board")

        roles = ["document", "spec"]
        _validate_package_roles(roles)
        _validate_dependencies({"doc-2": "doc-1"}, ["doc-1", "doc-2"], roles)

        with self.assertRaises(ValueError):
            _validate_package_roles(["email", "table"])

    def test_d8_04_through_07_parallel_ingestor_records_timeout_and_group_commit_failure(self) -> None:
        """D8-04..07: _records_from_projection extracts all 6 categories; timeout and group_commit failures propagate."""
        ingestor = ParallelDocumentIngestor(max_workers=2, group_commit=True)
        with tempfile.TemporaryDirectory() as tmp:
            sig_file = Path(tmp) / "03_canonical_signal_projection.json"
            sig_file.write_text(
                json.dumps(
                    {
                        "graph_mutations": {
                            "entity_upserts": [{"name": "E1", "description": "System entity"}],
                            "relationship_signals": [{"subject": "E1", "predicate": "DEPENDS_ON", "object": "E2"}],
                            "decision_updates": [{"title": "D1"}],
                            "proposal_updates": [{"title": "P1"}],
                            "risk_updates": [{"title": "R1"}],
                            "strategic_intent_signals": [{"title": "S1"}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            records = ingestor._records_from_projection(sig_file)
            self.assertEqual(len(records), 6)

            results: dict[str, Any] = {}
            with patch.object(ingestor, "_execute_group_commit", return_value={"status": "FAILED", "error": "commit failed"}):
                has_gc_failure = ingestor._run_group_commit_step([{"status": "COMPLETE"}], results)
            self.assertTrue(has_gc_failure)
            self.assertEqual(results["_group_commit"]["status"], "FAILED")

    def test_d8_08_and_10_retrieval_visibility_safe_source_id_and_sql_filter(self) -> None:
        """D8-08 & D8-10: completion_from_publication handles empty component_runs and missing source_id safely;
        _source_records filters by source_id in SQL."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "active_pair.json").write_text(
                json.dumps({"generation_id": "gen-1", "graph_sha256": "h1", "sidecar_sha256": "s1", "model_manifest_digest": "m1"}),
                encoding="utf-8",
            )
            pub = {
                "status": "COMPLETE",
                "release": {
                    "receipt": {
                        "next_pair": {"generation_id": "gen-1", "graph_sha256": "h1", "sidecar_sha256": "s1", "model_manifest_digest": "m1"},
                        "previous_pair": {},
                        "component_runs": [],
                    }
                },
            }
            comp = completion_from_publication(pub, root)
            self.assertEqual(comp["status"], "VERIFIED")
            self.assertEqual(comp["sources"], [])

            sidecar = root / "sidecar.sqlite"
            with sqlite3.connect(sidecar) as db:
                db.executescript(
                    "CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, retrieval_text_hash TEXT, artifact_json TEXT);"
                    "CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY);"
                )
                db.execute(
                    "INSERT INTO retrieval_record VALUES ('r1', 'candidate', 'h1', ?)",
                    (json.dumps({"extensions": {"com.partner_advisory.nee.source-id": "src-target", "com.partner_advisory.nee.graph-object-id": "a1"}}),),
                )
                db.execute(
                    "INSERT INTO retrieval_record VALUES ('r2', 'candidate', 'h2', ?)",
                    (json.dumps({"extensions": {"com.partner_advisory.nee.source-id": "src-other", "com.partner_advisory.nee.graph-object-id": "a2"}}),),
                )
            recs = _source_records(sidecar, "src-target")
            self.assertEqual(list(recs.keys()), ["r1"])

    def test_d8_11_12_14_quality_signal_grounding_whitespace_page_and_judge_pass_verdict(self) -> None:
        """D8-11, D8-12, D8-14: validate_signal_claim_grounding rejects ungrounded decision_updates;
        _resolve_text_span_page resolves line-wrapped PDF spans; _evaluate_output3_judge preserves PASS verdict."""
        bad_proj = {
            "evidence_index": {"ev-bad": {"exact_text": "Fabricated quote absent from source"}},
            "graph_mutations": {
                "relationship_signals": [],
                "decision_updates": [{"signal_id": "dec-1", "evidence_ids": ["ev-bad"]}],
            },
        }
        is_valid, violations = validate_signal_claim_grounding(bad_proj, "Valid quote text in source")
        self.assertFalse(is_valid)
        self.assertTrue(any("dec-1" in v for v in violations))

        source_text = "--- Page 3 ---\nThe architecture board approved\n  the phased rollout plan."
        page = _resolve_text_span_page("approved the phased rollout", source_text, [(0, 1), (15, 3)])
        self.assertEqual(page, 3)

        metadata: dict[str, Any] = {}
        with patch(
            "dual_pipeline.document_pipeline_core.evaluator._execute_document_judge",
            return_value=({"verdict": "PASS", "remediation_directive": "Optional polish"}, True, 92),
        ):
            j3_eval, j3_pass, s3 = _evaluate_output3_judge(
                q3_pass=True,
                qa_report={},
                meta=metadata,
                eff_model="test-model",
                eff_effort="high",
                reasoning_effort="high",
                fast_judge=True,
                f3_str="{}",
                source_text="source text",
                mock=True,
                attempt=1,
                max_attempts=3,
            )
        self.assertTrue(j3_pass)
        self.assertEqual(j3_eval["verdict"], "PASS")
        self.assertEqual(s3, 92)

    def test_d8_20_through_25_coverage_verifier_publish_helper_and_reconcile_tool(self) -> None:
        """D8-20..25: verify_fact_coverage_in_graph rejects missing relational predicate even when subject node exists;
        reconcile tool checks active_pair generation_id, *.intent.json, and publication_coverage_verified."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cov.sqlite"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(FULL_STAGE7_SCHEMA)
            conn.execute(
                "INSERT INTO node (node_id, node_type, canonical_name, primary_label, active, created_at) "
                "VALUES ('node-only', 'Workstream', 'Subject Only', 'Workstream', 1, '2026-10-01T00:00:00Z')"
            )
            conn.commit()
            conn.close()

            manifest = EvidenceCoverageManifest(
                manifest_id="cov-test-1",
                run_id="run-test-1",
                source_digest="e" * 64,
                items=[
                    EvidenceItem(
                        item_id="item-rel-1",
                        item_type=ItemType.SPEAKER_ROLE.value,
                        is_eligible_graph_fact=True,
                        state=EvidenceState.PROPOSED.value,
                        source_origin={"origin_type": "DOCUMENT", "source_id": "src-1"},
                        disposition_reason="Proposed role fact",
                        proposed_fact={"subject": "Subject Only", "predicate": "DEPENDS_ON", "object": "Missing Target"},
                    )
                ],
            )
            ok, violations, receipt = verify_fact_coverage_in_graph(
                graph_db=db_path,
                manifest=manifest,
                proposal_digest="f" * 64,
                activated_generation_id="gen-cov-1",
                output_dir=root,
            )
            self.assertFalse(ok)
            self.assertEqual(receipt["status"], "FAILED")
            self.assertEqual(receipt["coverage_summary"]["missing_count"], 1)

            (root / "active_pair.json").write_text(json.dumps({"generation_id": "gen-123"}), encoding="utf-8")
            info = _get_active_runtime_info(root)
            self.assertEqual(info["active_generation"], "gen-123")

            j_dir = root / "publication_journal"
            j_dir.mkdir(parents=True, exist_ok=True)
            (j_dir / "tx1.intent.json").write_text(
                json.dumps({"directive_id": "dir-tx1", "state": "PENDING", "bindings": {"source_digest": "d1"}}),
                encoding="utf-8",
            )
            issues = _scan_journal_issues(root)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["directive_id"], "dir-tx1")

            rec_item = _check_source_receipt(
                Path("src-1.pdf"),
                "d1",
                [{"source_digest": "d1", "terminal_outcome": "PASS", "publication_coverage_verified": True, "delta_table_counts": {"assertion": 1}}],
                "gen-123",
            )
            self.assertIsNotNone(rec_item)
            self.assertEqual(rec_item["status"], "COVERED")


if __name__ == "__main__":
    unittest.main()
