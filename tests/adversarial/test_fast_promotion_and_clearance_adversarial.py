"""7-category adversarial fault-injection tests for fast_promotion, transcript_turn_resolver, learning_commit, meeting_postprocess, clearance_cache, and capability_bridge."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from dual_pipeline.core import connect_sqlite, sha256_file
from dual_pipeline.graph_maintenance_core import transcript_turn_resolver
from dual_pipeline.graph_retrieval_core import capability_bridge, clearance_cache
from dual_pipeline.graph_writer_core import fast_promotion
from dual_pipeline.normalization_core import meeting_postprocess
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.uwg_core import learning_commit
from tools import prepare_evidence_envelope_intake


def _init_minimal_promotion_db(db_path: Path) -> None:
    conn = connect_sqlite(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, active INTEGER DEFAULT 1);
            CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT);
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                scope_text TEXT,
                object_literal TEXT,
                assertion_type TEXT,
                confidence_score REAL,
                qualifiers_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                retrieval_eligible INTEGER,
                currentness_state TEXT,
                validation_state TEXT,
                superseded_by_assertion_id TEXT,
                superseded_at TEXT,
                approval_reference TEXT,
                primary_source_id TEXT,
                speaker_or_author TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT,
                PRIMARY KEY (assertion_id, evidence_id)
            );
            """
        )
        conn.execute("INSERT INTO node VALUES ('n_subj', 'Project LENS', 'workstream', 1)")
        conn.execute("INSERT INTO node VALUES ('n_obj_1', 'Eduardo', 'person', 1)")
        conn.execute("INSERT INTO node VALUES ('n_obj_2', 'Speaker_Alpha', 'person', 1)")
        conn.execute("INSERT INTO source VALUES ('s_01', 'meeting_transcript', 'LENS Sync')")
        conn.commit()
    finally:
        conn.close()


# Category 1: Boundary Payloads
def test_adversarial_cat1_boundary_confidence_and_timestamps(tmp_path: Path) -> None:
    """Category 1 (Boundary payload): Verify confidence boundaries (0.0, 1.0] and extreme timestamp locators."""
    db_path = tmp_path / "boundary.sqlite"
    _init_minimal_promotion_db(db_path)

    with pytest.raises(ValueError, match="min_confidence"):
        fast_promotion.fast_promote_candidates(db_path, runtime_root=tmp_path, min_confidence=0.0)
    with pytest.raises(ValueError, match="min_confidence"):
        fast_promotion.fast_promote_candidates(db_path, runtime_root=tmp_path, min_confidence=1.01)
    with pytest.raises(ValueError, match="approval_reference"):
        fast_promotion.fast_promote_candidates(db_path, runtime_root=tmp_path, approval_reference="   ")

    # Boundary timestamps in transcript_turn_resolver
    assert transcript_turn_resolver.normalize_timestamp_locator(None) is None
    assert transcript_turn_resolver.normalize_timestamp_locator("") is None
    assert transcript_turn_resolver.normalize_timestamp_locator("[0:00]") == "00:00:00"
    assert transcript_turn_resolver.normalize_timestamp_locator("### [99:59:59]") == "99:59:59"


# Category 2: Malformed Input & Corrupt JSON/Schema
def test_adversarial_cat2_malformed_json_and_missing_tables(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Verify graceful handling of malformed companion JSON and missing tables."""
    md_path = tmp_path / "01_malformed_transcript.md"
    md_path.write_text("# Empty transcript without speaker turns\nJust prose.", encoding="utf-8")
    bad_json = tmp_path / "03_bad_canonical_signal_projection.json"
    bad_json.write_text("{corrupt_json_payload", encoding="utf-8")

    assert transcript_turn_resolver._load_companion_verified_speakers(md_path) == {}
    assert (
        transcript_turn_resolver.resolve_diarized_transcript_turn_speaker(
            str(md_path), "00:01:00", "Short text"
        )
        is None
    )

    # Empty SQLite database without node/assertion tables in capability_bridge
    empty_db = tmp_path / "empty_schema.sqlite"
    conn = connect_sqlite(empty_db)
    try:
        assert capability_bridge.collect_csr_virtual_edge_pairs(conn) == set()
    finally:
        conn.close()


# Category 3: Concurrency & Race Conditions
def test_adversarial_cat3_concurrent_clearance_cache_and_serializer(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): Parallel threads querying FTS5 BM25 cache and write serializer."""
    clearance_cache.clear_clearance_caches()
    db_path = tmp_path / "concurrent_fts.sqlite"
    conn = connect_sqlite(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY,
                search_document_id TEXT,
                assertion_id TEXT,
                retrieval_eligible INTEGER,
                currentness_state TEXT
            );
            CREATE VIRTUAL TABLE search_fts USING fts5(title, body);
            INSERT INTO search_document VALUES (1, 'sd_1', 'asrt_1', 1, 'current');
            INSERT INTO search_fts(rowid, title, body) VALUES (1, 'LENS S3', 'Amazon S3 drone inspection imagery');
            """
        )
        conn.commit()
    finally:
        conn.close()

    errors: list[Exception] = []

    def _worker(idx: int) -> None:
        try:
            for _ in range(10):
                ok, matches = clearance_cache.fts5_bm25_matches_cached(
                    db_path, "Amazon S3 drone imagery", limit=10
                )
                assert ok is True
                assert matches == ["asrt_1"]
                if idx % 4 == 0:
                    clearance_cache.clear_clearance_caches()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []


# Category 4: Overflow & Batch Exhaustion
def test_adversarial_cat4_batch_candidate_dedup_and_hub_cap(tmp_path: Path) -> None:
    """Category 4 (Overflow & exhaustion): Large candidate batch deduplication and capability hub cap."""
    raw_candidates = [
        {
            "candidate_id": f"cand_{i}",
            "candidate_type": "assertion",
            "normalized_text": "Project LENS integrates Skydio drone telemetry into S3.",
            "evidence_ids": [f"ev_{i}"],
            "entity_mentions": [{"reported_text": f"Entity_{i % 5}", "stable_identifier": None}],
        }
        for i in range(120)
    ]
    deduped = meeting_postprocess.deduplicate_candidates(raw_candidates)
    assert len(deduped) == 1
    assert len(deduped[0]["evidence_ids"]) == 120
    assert len(deduped[0]["entity_mentions"]) == 5

    # Capability bridge caps domain hubs at 4 even when 25 technical anchors exist
    many_nodes = {
        f"n_{i:02d}": {"canonical_name": f"Drone Sensor {i}", "node_type": "technical_anchor"}
        for i in range(25)
    }
    hubs = capability_bridge._select_domain_hubs(list(many_nodes.keys()), many_nodes, limit=4)
    assert len(hubs) == 4


# Category 5: Invariant Bypass Attempts
def test_adversarial_cat5_invariant_bypass_forbidden_qualifiers_and_nonhuman_approval(
    tmp_path: Path,
) -> None:
    """Category 5 (Invariant bypass attempt): Block forbidden qualifiers, mutex predicate conflicts, and non-human UWG approvals."""
    db_path = tmp_path / "bypass.sqlite"
    _init_minimal_promotion_db(db_path)
    conn = connect_sqlite(db_path)
    try:
        # Canonical mutex assertion: n_subj OWNED_BY n_obj_1
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_canon_owner', 'n_subj', 'OWNED_BY', 'n_obj_1',
                'Project LENS is owned by Eduardo.', 'Eduardo', 'fact', 0.95, '{}',
                '2026-10-30T09:00:00Z', '2026-10-30T09:00:00Z', 'canonical', 1, 1,
                'current', 'verified', NULL, NULL, 'APP-0', 's_01', 'Eduardo'
            )"""
        )
        # Candidate attempting conflicting OWNED_BY n_obj_2 (must be blocked by mutex gate)
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_cand_mutex_conflict', 'n_subj', 'OWNED_BY', 'n_obj_2',
                'Project LENS is owned by Speaker_Alpha.', 'Speaker_Alpha', 'fact', 0.95, '{}',
                '2026-10-30T09:05:00Z', '2026-10-30T09:05:00Z', 'candidate', 0, 1,
                'current', 'verified', NULL, NULL, NULL, 's_01', 'Eduardo'
            )"""
        )
        # Candidate with forbidden citation_only qualifier (must be blocked)
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_cand_forbidden_qual', 'n_subj', 'HAS_CLAIM_TEXT', NULL,
                'Restricted citation only claim.', 'Restricted claim', 'fact', 0.95,
                '{"answer_use":"citation_only"}', '2026-10-30T09:10:00Z', '2026-10-30T09:10:00Z',
                'candidate', 0, 1, 'current', 'verified', NULL, NULL, NULL, 's_01', 'Eduardo'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_cand_mutex_conflict', 'ev_m', 'supports')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_cand_forbidden_qual', 'ev_q', 'supports')")
        conn.commit()
    finally:
        conn.close()

    res = fast_promotion.fast_promote_candidates(db_path, runtime_root=tmp_path, min_confidence=0.80)
    assert res.status == "NOOP"
    assert res.promoted_count == 0
    assert res.skipped_count == 2

    # Non-human approval origin in UWG learning commit must raise PermissionError
    with patch(
        "dual_pipeline.hitl_core.envelope.validate_approval_origin",
        autospec=True,
        return_value=(False, "synthetic_auto_hook"),
    ):
        with pytest.raises(PermissionError, match="UWG_COMMIT_REJECTED"):
            learning_commit._parse_learning_proposal_and_approval(
                {
                    "proposal": {"proposal_id": "p_1"},
                    "approval_receipt": {"receipt_id": "r_1"},
                }
            )


# Category 6: Rollback & Mid-Flight Fault Recovery
def test_adversarial_cat6_atomic_byte_restore_and_parameter_rollback(tmp_path: Path) -> None:
    """Category 6 (Rollback & recovery): Verify byte-exact DB restoration on promotion proof failure and parameter rollback on active_pair mismatch."""
    db_path = tmp_path / "rollback_promo.sqlite"
    _init_minimal_promotion_db(db_path)
    conn = connect_sqlite(db_path)
    try:
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_cand_valid', 'n_subj', 'HAS_CLAIM_TEXT', NULL,
                'Valid candidate claim for rollback test.', 'Valid claim', 'fact', 0.92, '{}',
                '2026-10-30T09:15:00Z', '2026-10-30T09:15:00Z', 'candidate', 0, 1,
                'current', 'verified', NULL, NULL, NULL, 's_01', 'Eduardo'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_cand_valid', 'ev_v', 'supports')")
        conn.commit()
    finally:
        conn.close()

    pre_sha = sha256_file(db_path)
    with patch(
        "dual_pipeline.graph_writer_core.fast_promotion.prove",
        autospec=True,
        return_value={"overall": "FAIL", "checks": [{"check": "readback", "status": "FAIL"}]},
    ):
        with pytest.raises(fast_promotion.GraphMutationProofError):
            fast_promotion.fast_promote_candidates(db_path, runtime_root=tmp_path, min_confidence=0.80)

    # Database bytes must be restored identically after proof failure
    assert sha256_file(db_path) == pre_sha

    # Parameter rollback when _verify_active_pair_unchanged raises mid-apply
    cfg_path = tmp_path / "rollback_params.json"
    reg = ParameterRegistry(config_path=cfg_path)
    reg.set("hitl.ambiguity_margin_threshold", 0.20, persist=True, config_path=cfg_path)

    class _FailingGateway:
        def _verify_active_pair_unchanged(self, *args: Any) -> None:
            raise RuntimeError("Simulated active_pair mutation during parameter apply")

    with pytest.raises(RuntimeError, match="Simulated active_pair mutation"):
        learning_commit._apply_parameters_with_rollback(
            reg,
            cfg_path,
            applied_params={"hitl.ambiguity_margin_threshold": 0.15, "copilot.max_speakable_words": 38},
            prev_params={"hitl.ambiguity_margin_threshold": 0.20},
            absent_params=["copilot.max_speakable_words"],
            gateway=_FailingGateway(),
            active_before={},
            graph_sha256="a" * 64,
            generation_id="gen_1",
        )
    reg_check = ParameterRegistry(config_path=cfg_path)
    assert reg_check.get("hitl.ambiguity_margin_threshold") == pytest.approx(0.20)
    found_absent, _ = reg_check._lookup_dotted(reg_check._load_json(), "copilot.max_speakable_words")
    assert found_absent is False


# Category 7: Exact Historical Production Regression Escapes (HIST-01)
def test_adversarial_cat7_hist01_sidecar_binding_and_timestamp_matching(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): Verify sidecar SHA mismatch rejection and timestamp locator matching."""
    sidecar_db = tmp_path / "sidecar.sqlite"
    graph_db = tmp_path / "graph.sqlite"
    connect_sqlite(sidecar_db).close()
    connect_sqlite(graph_db).close()

    with pytest.raises(RuntimeError, match="not bound to the active graph"):
        clearance_cache._load_cleared_lane_records(
            sidecar_db,
            graph_db,
            validate_fn=lambda _: {"metadata": {"graph_sha256": "0" * 64}},
        )

    assert prepare_evidence_envelope_intake._match_timestamp_locator(
        "00:05:12", "Discussion at [5:12] covered S3 buckets."
    ) is True
    assert prepare_evidence_envelope_intake._match_timestamp_locator(
        "00:05:12", "Notes without any timestamps."
    ) is False
