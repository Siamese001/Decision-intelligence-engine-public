"""Adversarial & 7-Category Fault-Injection Suite for Sidecar Vector Recovery, Hygiene Promotion, Option Nuance & Watchdogs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.learning_engine_core.option_nuance import (
    _build_synthesized_entry,
    _collect_healing_option_entries,
    _collect_option_stats,
    synthesize_hitl_option_nuance,
)
from dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup import (
    _auto_commit_worktree_deliverables,
)
from tools.scheduled_task_core.adapters_core.watchdogs import run_timeout_lock_watchdog
from tools.scheduled_task_core.authority import (
    AuthorityContext,
    ScheduledMutationBlockedError,
)
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.tier2_dedup_core.hygiene_promotion import (
    archive_extra_stale_candidates,
    remediate_auto_link_edges,
    remediate_orphan_nodes,
)
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _backfill_colbert_tokens,
    _copy_from_single_donor,
    _ensure_missing_dense_vectors,
    _load_sidecar_record_meta,
    _tokenize_retrieval_text,
    remediate_additional_hygiene_fixes,
    repair_falsely_historical_merged_assertions,
)


def test_category1_boundary_empty_inputs_and_zero_dimensions(tmp_path: Path) -> None:
    """Category 1 (Boundary payload): Empty strings, zero-dimension ColBERT config, and empty findings."""
    assert _tokenize_retrieval_text("") == {"record": 1}
    assert _build_synthesized_entry("canon_k", {"affirmed_count": 0, "rejected_count": 0, "category": "gen"}) is None

    db_path = tmp_path / "empty_sidecar.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO metadata VALUES ('colbert_dimensions', '0');
            CREATE TABLE colbert_token (
                record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB
            );
            """
        )
        _backfill_colbert_tokens(conn, ["rec-1"], {"rec-1": ("h1", "valid tokens")}, {"metadata", "colbert_token"})
        cb_cnt = conn.execute("SELECT COUNT(*) FROM colbert_token").fetchone()[0]
        assert cb_cnt == 0
        assert repair_falsely_historical_merged_assertions(conn, "2026-10-27T00:00:00Z") == 0


def test_category2_malformed_json_and_corrupt_schema_inputs(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Truncated JSON in retrieval_record and non-dict HITL history entries."""
    db_path = tmp_path / "malformed.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, retrieval_text_hash TEXT, artifact_json TEXT)"
        )
        conn.execute("INSERT INTO retrieval_record VALUES ('rec-bad', NULL, '{truncated_json')")
        meta = _load_sidecar_record_meta(conn, {"retrieval_record"})
        assert meta["rec-bad"] == ("rec-bad", "rec-bad")

    malformed_history = [
        None,
        42,
        "invalid_record",
        {"operator_id": "human_op", "selected_option": "", "options": [{}, {"label": ""}, ""]},
    ]
    assert _collect_option_stats(malformed_history) == {}  # type: ignore[arg-type]
    assert _collect_healing_option_entries(tmp_path / "nonexistent_healing.sqlite") == {}


def test_category3_concurrent_wal_sidecar_recovery_and_ledger_reads(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): Parallel WAL readers and writers without OperationalError lock failure."""
    db_path = tmp_path / "concurrent_sidecar.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(
            """
            CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB);
            CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token));
            """
        )

    def _worker(idx: int) -> int:
        with sqlite3.connect(db_path, timeout=30.0) as wconn:
            wconn.execute("PRAGMA busy_timeout=30000;")
            _ensure_missing_dense_vectors(wconn, [f"rec-concurrent-{idx}"])
            return wconn.execute("SELECT COUNT(*) FROM dense_vector").fetchone()[0]

    with patch(
        "tools.tier2_dedup_core.sidecar_vector_recovery.resolve_authoritative_runtime_root",
        autospec=True,
        side_effect=RuntimeError("no donor"),
    ):
        with ThreadPoolExecutor(max_workers=4) as pool:
            counts = list(pool.map(_worker, range(8)))

    assert max(counts) == 8


def test_category4_token_budget_and_sparse_weight_cap_overflow(tmp_path: Path) -> None:
    """Category 4 (Overflow & exhaustion): >40-char tokens filtered and sparse weights capped at 24 tokens."""
    long_tok = "a" * 41
    many_tokens = " ".join(f"tok{i:02d}" for i in range(40)) + f" {long_tok}"
    db_path = tmp_path / "overflow.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, retrieval_text_hash TEXT, artifact_json TEXT);
            CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB);
            CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token));
            CREATE TABLE colbert_token (
                record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB,
                PRIMARY KEY (record_id, position)
            );
            """
        )
        conn.execute(
            "INSERT INTO retrieval_record VALUES ('rec-ovf', 'h-ovf', ?)",
            (f'{{"retrieval_text": "{many_tokens}"}}',),
        )
        with patch(
            "tools.tier2_dedup_core.sidecar_vector_recovery.resolve_authoritative_runtime_root",
            autospec=True,
            side_effect=RuntimeError("isolated"),
        ):
            _ensure_missing_dense_vectors(conn, ["rec-ovf"])
        sw_cnt = conn.execute("SELECT COUNT(*) FROM sparse_weight WHERE record_id='rec-ovf'").fetchone()[0]
        cb_cnt = conn.execute("SELECT COUNT(*) FROM colbert_token WHERE record_id='rec-ovf'").fetchone()[0]
        has_long = conn.execute(
            "SELECT COUNT(*) FROM sparse_weight WHERE token = ?", (long_tok,)
        ).fetchone()[0]

    assert sw_cnt == 24
    assert cb_cnt == 4
    assert has_long == 0


def test_category5_invariant_bypass_autonomous_poisoning_and_scheduled_mutation(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass attempt): Blocks autonomous operator poisoning and scheduled mutation bypass."""
    poisoned_history = [
        {
            "operator_id": "autonomous_engine",
            "category": "governance",
            "selected_option": "Auto Approve",
            "options": ["Auto Approve", "Reject"],
        },
        {
            "operator_id": "human_spoof",
            "metadata": {"operator_class": "AUTONOMOUS"},
            "category": "governance",
            "selected_option": "Auto Approve",
            "options": ["Auto Approve", "Reject"],
        },
    ]
    assert _collect_option_stats(poisoned_history) == {}

    ctx = AuthorityContext(
        task_id="testing_gaps_assurance",
        run_id="run_adv",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="cron",
        is_scheduled=True,
    )
    with pytest.raises(ScheduledMutationBlockedError):
        ctx.assert_can_mutate("graph_write")
    with pytest.raises(ScheduledMutationBlockedError):
        ctx.check_caller_scheduled_bypass(caller_scheduled=False)

    stale_ws = tmp_path / "stale_lock_ws"
    stale_ws.mkdir(parents=True)
    (stale_ws / "stale.lock").write_text("locked", encoding="utf-8")
    wd_res = run_timeout_lock_watchdog(ctx, stale_ws, tmp_path / "wd_adv_run")
    assert wd_res.terminal_outcome == "HITL_REQUIRED"


def test_category6_fault_injection_donor_corruption_and_trigger_recovery(tmp_path: Path) -> None:
    """Category 6 (Rollback & recovery): Fault injection on corrupt donor DB and search_document_ad trigger recovery."""
    corrupt_donor = tmp_path / "corrupt_donor.sqlite"
    corrupt_donor.write_bytes(b"NOT_A_SQLITE_FILE")

    target_db = tmp_path / "target_recovery.sqlite"
    with sqlite3.connect(target_db) as sconn:
        copied = _copy_from_single_donor(sconn, corrupt_donor, b"\x00" * 4096, {"rec-1"}, {"dense_vector"})
        assert copied == set()

        sconn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, active INTEGER, status TEXT, updated_at TEXT);
            CREATE TABLE entity_redirect (from_node_id TEXT, to_node_id TEXT, active INTEGER);
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, object_node_id TEXT,
                canonical_status TEXT, currentness_state TEXT, validation_state TEXT,
                approval_reference TEXT, canonical_eligible INTEGER, retrieval_eligible INTEGER,
                primary_source_id TEXT, superseded_by_assertion_id TEXT, superseded_at TEXT, updated_at TEXT
            );
            CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT,
                object_type TEXT, assertion_id TEXT, canonical_status TEXT,
                currentness_state TEXT, source_id TEXT, retrieval_eligible INTEGER, updated_at TEXT
            );
            CREATE TRIGGER search_document_ad AFTER DELETE ON search_document BEGIN
                SELECT 1;
            END;
            """
        )
        sconn.execute(
            "INSERT INTO search_document (search_document_id, object_type, assertion_id, canonical_status, currentness_state, source_id, retrieval_eligible, updated_at) "
            "VALUES ('asrt-1', 'assertion', 'asrt-1', 'canonical', 'current', 'src-1', 1, '2026-01-01')"
        )
        sconn.execute(
            "INSERT INTO search_document (search_document_id, object_type, assertion_id, canonical_status, currentness_state, source_id, retrieval_eligible, updated_at) "
            "VALUES ('claim-dup', 'claim', 'asrt-1', 'canonical', 'current', 'src-1', 1, '2026-01-01')"
        )
        repair_falsely_historical_merged_assertions(sconn, "2026-10-27T00:00:00Z")
        trig_exists = sconn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='search_document_ad'"
        ).fetchone()[0]
        claim_dup_cnt = sconn.execute(
            "SELECT COUNT(*) FROM search_document WHERE object_type='claim'"
        ).fetchone()[0]

    assert trig_exists == 1
    assert claim_dup_cnt == 0


def test_category7_exact_production_regression_hist01_hist10_and_self_loop_guard(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): HIST-10 subprocess timeout error & authored-code teardown abort."""
    # 1. Subprocess TimeoutExpired fail-closed handling
    with patch(
        "dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup.subprocess.run",
        autospec=True,
        side_effect=subprocess.TimeoutExpired(cmd=["git", "status"], timeout=30.0),
    ):
        assert _auto_commit_worktree_deliverables(tmp_path, "ingest/meeting-nexus") is False

    # 2. Uncommitted authored code files abort teardown (return False)
    porcelain_authored = b"1 .M N... 100644 100644 100644 0000 1111 dual_pipeline/core.py\x00"
    with patch(
        "dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup.subprocess.run",
        autospec=True,
        return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout=porcelain_authored, stderr=b""),
    ):
        assert _auto_commit_worktree_deliverables(tmp_path, "ingest/meeting-nexus") is False

    # 3. Strict package gating blocks archive_extra_stale_candidates and unrequested repairs
    db_path = tmp_path / "strict_gate.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, created_at TEXT, canonical_status TEXT)")
        archive_extra_stale_candidates(conn, {"strict_package_gating": True}, ["a-1"], False, "2026-10-27T00:00:00Z")
        res = remediate_additional_hygiene_fixes(conn, {"strict_package_gating": True, "findings": []}, "2026-10-27T00:00:00Z", [])
        assert res["falsely_historical_assertions_restored"] == 0
        assert remediate_auto_link_edges(conn, {"findings": [{}]}, "2026-10-27T00:00:00Z", []) == 0
        assert remediate_orphan_nodes(conn, {"findings": [{}]}, "2026-10-27T00:00:00Z", []) == (0, [])

    # 4. synthesize_hitl_option_nuance handles missing ledger file cleanly
    out_json = tmp_path / "empty_nuance.json"
    res_nuance = synthesize_hitl_option_nuance(tmp_path / "missing_ledger.json", out_json)
    assert res_nuance["total_learned_options"] == 0
