"""End-to-end lifecycle tests for sidecar vector donor recovery, hygiene promotion, and option nuance recalibration."""
from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.index_core.constants import SIDECAR_SCHEMA
from dual_pipeline.learning_engine_core.option_nuance import (
    recalibrate_post_learning_confidences_core,
)
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore
from tools.scheduled_task_core.adapters_core.watchdogs import run_testing_gaps_assurance
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.tier2_dedup_core.hygiene_promotion import (
    remediate_auto_link_edges,
    remediate_orphan_nodes,
)
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _DONOR_GENERATIONS,
    _deterministic_unit_vector,
    _ensure_missing_dense_vectors,
    remediate_additional_hygiene_fixes,
    repair_falsely_historical_merged_assertions,
)


def test_e2e_donor_sidecar_vector_recovery_and_hygiene_lifecycle(tmp_path: Path) -> None:
    """E2E lifecycle: recovers real vectors from donor generation sidecar and executes full hygiene remediation."""
    rt_root = tmp_path / "runtime"
    donor_gen = rt_root / "generations" / _DONOR_GENERATIONS[0]
    donor_gen.mkdir(parents=True)
    donor_sidecar = donor_gen / "sidecar.sqlite"

    real_vec = _deterministic_unit_vector("donor-authentic-embedding", dim=1024)
    unit_blob = struct.pack("<1024f", 1.0, *([0.0] * 1023))

    with sqlite3.connect(donor_sidecar) as dconn:
        dconn.executescript(SIDECAR_SCHEMA)
        dconn.execute(
            "INSERT INTO retrieval_record VALUES ('rec-donor-1', 'approved', 'assertion', 'CLIENT_SAFE', 'h1', 'assertion', 'approved', 'direct_source', 'factual_answer', '{}')"
        )
        dconn.execute("INSERT INTO dense_vector VALUES ('rec-donor-1', 1024, ?)", (real_vec,))
        dconn.execute("INSERT INTO sparse_weight VALUES ('rec-donor-1', 'substation', 0.95)")

    target_sidecar = tmp_path / "target_sidecar.sqlite"
    with sqlite3.connect(target_sidecar) as sconn:
        sconn.executescript(SIDECAR_SCHEMA)
        sconn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', '64')")
        sconn.execute(
            "INSERT INTO retrieval_record VALUES ('rec-donor-1', 'approved', 'assertion', 'CLIENT_SAFE', 'h1', 'assertion', 'approved', 'direct_source', 'factual_answer', ?)"
            , (json.dumps({"retrieval_text": "Substation voltage transformer capacity"}),),
        )
        sconn.execute(
            "INSERT INTO retrieval_record VALUES ('rec-fresh-2', 'approved', 'assertion', 'CLIENT_SAFE', 'h2', 'assertion', 'approved', 'direct_source', 'factual_answer', ?)"
            , (json.dumps({"claim_text": "Transmission feeder thermal rating verified"}),),
        )
        # Seed dummy unit vector on rec-donor-1 so donor recovery replaces it
        sconn.execute("INSERT INTO dense_vector VALUES ('rec-donor-1', 1024, ?)", (unit_blob,))
        sconn.execute("INSERT INTO sparse_weight VALUES ('rec-donor-1', 'entity', 1.0)")

        with patch(
            "tools.tier2_dedup_core.sidecar_vector_recovery.resolve_authoritative_runtime_root",
            autospec=True,
            return_value=rt_root,
        ):
            _ensure_missing_dense_vectors(sconn, ["rec-donor-1", "rec-fresh-2"])

    # Independent read-only state verification
    with sqlite3.connect(f"file:{target_sidecar}?mode=ro", uri=True) as ro_conn:
        integrity = ro_conn.execute("PRAGMA integrity_check").fetchone()[0]
        rec1_vec = ro_conn.execute("SELECT vector FROM dense_vector WHERE record_id='rec-donor-1'").fetchone()[0]
        rec2_vec = ro_conn.execute("SELECT vector FROM dense_vector WHERE record_id='rec-fresh-2'").fetchone()[0]
        rec1_sw = dict(ro_conn.execute("SELECT token, weight FROM sparse_weight WHERE record_id='rec-donor-1'").fetchall())
        cb_cnt = ro_conn.execute("SELECT COUNT(DISTINCT record_id) FROM colbert_token").fetchone()[0]

    assert integrity == "ok"
    assert rec1_vec == real_vec
    assert rec2_vec != unit_blob and len(rec2_vec) == 4096
    assert rec1_sw.get("substation") == 0.95
    assert "entity" not in rec1_sw
    assert cb_cnt == 2


def test_e2e_hygiene_promotion_and_redirect_repoint_pipeline(tmp_path: Path) -> None:
    """E2E lifecycle: executes orphan node archival, auto-link edges, and falsely-historical redirect restoration."""
    graph_db = tmp_path / "canonical_graph.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, active INTEGER, status TEXT, updated_at TEXT);
            CREATE TABLE node_state (
                node_id TEXT PRIMARY KEY, lifecycle_state TEXT, currentness_state TEXT,
                retrieval_eligibility TEXT, resolution_state TEXT, updated_at TEXT
            );
            CREATE TABLE entity_redirect (
                from_node_id TEXT, to_node_id TEXT, merge_event_id TEXT, active INTEGER, created_at TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT,
                scope_text TEXT, canonical_status TEXT, canonical_eligible INTEGER, retrieval_eligible INTEGER,
                currentness_state TEXT, validation_state TEXT, approval_reference TEXT, confidence_score REAL,
                primary_source_id TEXT, superseded_by_assertion_id TEXT, superseded_at TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT, created_at TEXT
            );
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY, from_node_id TEXT, edge_type TEXT, to_node_id TEXT,
                description TEXT, source_id TEXT
            );
            CREATE TABLE legacy_edge_assertion_map (
                edge_id TEXT PRIMARY KEY, assertion_id TEXT, migration_confidence REAL,
                migration_state TEXT, created_at TEXT
            );
            CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT,
                object_type TEXT, object_id TEXT, assertion_id TEXT, title TEXT, body TEXT,
                canonical_status TEXT, currentness_state TEXT, source_id TEXT,
                retrieval_eligible INTEGER, updated_at TEXT
            );
            CREATE VIRTUAL TABLE search_fts USING fts5(
                title, body, object_type, object_id,
                content='search_document', content_rowid='search_rowid'
            );
            """
        )
        conn.execute("INSERT INTO node VALUES ('n-orphan', 'entity', 1, 'canonical', '2026-01-01')")
        conn.execute("INSERT INTO node_state VALUES ('n-orphan', 'active', 'current', 'eligible', 'resolved', '2026-01-01')")
        conn.execute("INSERT INTO node VALUES ('n-merged', 'entity', 0, 'inactive', '2026-01-01')")
        conn.execute("INSERT INTO node VALUES ('n-canon', 'entity', 1, 'canonical', '2026-01-01')")
        conn.execute("INSERT INTO node VALUES ('n-target', 'entity', 1, 'canonical', '2026-01-01')")
        conn.execute("INSERT INTO entity_redirect VALUES ('n-merged', 'n-canon', 'me-1', 1, '2026-01-01')")
        conn.execute(
            "INSERT INTO assertion VALUES ('asrt-hist', 'n-merged', 'GOVERNED_BY', 'n-target', 'scope', "
            "'historical', 0, 0, 'historical', 'needs_review', NULL, 0.92, 'src-1', NULL, NULL, '2026-01-01', '2026-01-01')"
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-hist', 'ev-1', 'supports', '2026-01-01')")
        conn.execute(
            "INSERT INTO search_document (search_document_id, object_type, object_id, assertion_id, title, body, canonical_status, currentness_state, source_id, retrieval_eligible, updated_at) "
            "VALUES ('sd-node', 'node', 'n-orphan', NULL, 'Orphan', 'Body', 'canonical', 'current', 'src-1', 1, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO search_document (search_document_id, object_type, object_id, assertion_id, title, body, canonical_status, currentness_state, source_id, retrieval_eligible, updated_at) "
            "VALUES ('asrt-hist', 'assertion', 'asrt-hist', 'asrt-hist', 'Title', 'Body', 'historical', 'historical', 'src-1', 0, '2026-01-01')"
        )

        pkg = {
            "findings": [
                {"proposed_fix": "archive_node", "raw_data": {"node_id": "n-orphan"}},
                {"proposed_fix": "auto_link_edge", "raw_data": {"edge_id": "edge-99", "matching_assertion_id": "asrt-hist"}},
                {"proposed_fix": "escalate_90d_revalidation", "raw_data": {"assertion_id": ["asrt-stale"]}},
            ]
        }
        sample_ids: list[str] = []
        now_iso = "2026-10-27T05:00:00Z"
        archived_cnt, archived_list = remediate_orphan_nodes(conn, pkg, now_iso, sample_ids)
        linked_cnt = remediate_auto_link_edges(conn, pkg, now_iso, sample_ids)
        restored_cnt = repair_falsely_historical_merged_assertions(conn, now_iso)
        extra_res = remediate_additional_hygiene_fixes(conn, pkg, now_iso, sample_ids)

    with sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True) as ro:
        orphan_status = ro.execute("SELECT active, status FROM node WHERE node_id='n-orphan'").fetchone()
        asrt_state = ro.execute(
            "SELECT canonical_status, currentness_state, retrieval_eligible FROM assertion WHERE assertion_id='asrt-hist'"
        ).fetchone()
        fts_eligible_cnt = ro.execute("SELECT COUNT(*) FROM search_fts WHERE search_fts MATCH 'Title'").fetchone()[0]
        fts_orphan_cnt = ro.execute("SELECT COUNT(*) FROM search_fts WHERE search_fts MATCH 'Orphan'").fetchone()[0]

    assert archived_cnt == 1 and archived_list == ["n-orphan"]
    assert linked_cnt == 1
    assert restored_cnt >= 1
    assert extra_res["revalidation_90d_archived"] == 1
    assert orphan_status == (0, "archived_orphan")
    assert asrt_state == ("canonical", "current", 1)
    assert fts_eligible_cnt == 1
    assert fts_orphan_cnt == 0


def test_e2e_post_learning_confidence_recalibration_with_healing_store(tmp_path: Path) -> None:
    """E2E lifecycle: combines HITLDecisionStore and HealingDecisionStore into recalibrated runtime stores."""
    rt_root = tmp_path / "runtime"
    (rt_root / "config" / "runtime").mkdir(parents=True)
    (rt_root / "ledger").mkdir(parents=True)

    ledger_path = rt_root / "ledger" / "hitl_decision_ledger.json"
    hitl_store = HITLDecisionStore(ledger_path=ledger_path)
    hitl_store.record_decision(
        decision_id="q-e2e-1",
        prompt="Proceed with governed release?",
        selected_option="Execute Governed Merge",
        operator_id="operator_primary",
        category="release_governance",
        options=["Execute Governed Merge", "Hold Release"],
        rationale="All 32 gates passed",
    )

    healing_db = rt_root / "ledger" / "learning.sqlite"
    h_store = HealingDecisionStore(db_path=healing_db)
    h_store.record_decision(
        {
            "run_id": "run-heal-1",
            "subsystem": "transcript_qa",
            "component": "transcript_qa",
            "scope": "X1_QA",
            "repair_class": "date_header_normalization",
            "action_taken": "NORMALIZE_DATE_HEADER",
            "outcome_status": "HEALED",
            "before_digest": "b1234567890",
            "after_digest": "a1234567890",
        }
    )

    res = recalibrate_post_learning_confidences_core(
        runtime_root=rt_root,
        proposals_dir=tmp_path / "proposals",
        hitl_ledger_path=ledger_path,
        healing_db_path=healing_db,
    )

    assert res["status"] == "RECALIBRATED"
    assert res["total_learned_options"] >= 2
    learned_file = rt_root / "learned_hitl_option_store.json"
    posteriors_file = rt_root / "learned_healing_posteriors.json"
    assert learned_file.is_file()
    assert posteriors_file.is_file()

    saved_options = json.loads(learned_file.read_text(encoding="utf-8"))["options"]
    assert "date_header_normalization" in saved_options
    assert saved_options["date_header_normalization"]["category"] == "healing_policy"

    ctx = AuthorityContext(
        task_id="testing_gaps_assurance",
        run_id="run_e2e_wd",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator_primary",
        is_scheduled=False,
    )
    wd_res = run_testing_gaps_assurance(ctx, tmp_path / "clean_repo", tmp_path / "wd_run")
    assert wd_res.terminal_outcome == "COMPLETE"
