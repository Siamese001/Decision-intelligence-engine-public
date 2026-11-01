"""
Adversarial and concurrency tests for Wave 6: Graph Hygiene Proof Integrity & Storage Serialization (CAT-5).

Covers:
1. Rollback & recovery / Invariant bypass: Mismatched expected row deltas fail physical proof closed with strict_delta=True.
2. Concurrency & race: 16-worker parallel SQLite write stress test via create_sqlite_conn and execute_with_retry without OperationalError.
3. Malformed / boundary payload: Entity redirect chain and empty/whitespace role rejection in UWG.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pytest

from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    ProductionPurityViolationError,
    _REPO_ROOT,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import (
    _find_subject_node_id,
    persist_validated_role,
)
from storage.connection import create_sqlite_conn, execute_with_retry
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation
from tools.tier2_dedup_core.document_graph_parity_core import (
    promote_all_candidates_and_empty_sources,
    remediate_boilerplate_assertions,
)


def test_mismatched_expected_deltas_fail_physical_proof_closed(tmp_path: Path) -> None:
    from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph

    db_path = tmp_path / "adversarial_delta.sqlite"
    _bootstrap_test_graph(db_path)

    pkg_path = tmp_path / "pkg_mismatch.json"
    pkg_path.write_text(
        json.dumps(
            {
                "findings": [],
                "expected_deltas": {
                    "audit_event": 1,
                    "entity_merge_event": 5,  # Expected 5 merges, but 0 occurred
                },
            }
        ),
        encoding="utf-8",
    )

    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path, strict_delta=True)
    assert receipt["terminal_outcome"] == "FAIL"
    assert receipt["physical_proof"]["overall"] == "FAIL"
    row_delta_chk = next(c for c in receipt["physical_proof"]["checks"] if c["check"] == "row_delta")
    assert row_delta_chk["status"] == "FAIL"


def test_16_worker_concurrent_sqlite_writes_zero_lock_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent_16_workers.sqlite"
    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute("CREATE TABLE stress_log (worker_id INTEGER, seq INTEGER, payload TEXT)")
        conn.commit()
    finally:
        conn.close()

    def _worker_task(worker_id: int) -> int:
        written = 0
        for seq in range(10):
            with GovernedStorageContext.write_context(db_path) as wconn:
                execute_with_retry(
                    wconn,
                    "INSERT INTO stress_log (worker_id, seq, payload) VALUES (?, ?, ?)",
                    (worker_id, seq, f"worker-{worker_id}-seq-{seq}"),
                )
                written += 1
        return written

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_worker_task, range(16)))

    assert sum(results) == 160
    ro_conn = create_sqlite_conn(db_path, readonly=True)
    try:
        total = ro_conn.execute("SELECT COUNT(*) FROM stress_log").fetchone()[0]
        assert total == 160
    finally:
        ro_conn.close()


def test_entity_redirect_resolution_and_parity_no_hex_or_synthetic_backfill(tmp_path: Path) -> None:
    from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph

    db_path = tmp_path / "redirect_and_parity.sqlite"
    _bootstrap_test_graph(db_path)

    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute(
            "INSERT INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) "
            "VALUES ('person_danny_o', 'stakeholder_dan_okray', 'm1', 1, '2026-10-20T00:00:00Z')"
        )
        conn.execute(
            "CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT, source_date TEXT, notes TEXT)"
        )
        conn.execute(
            "INSERT INTO source (source_id, source_type, source_name, source_date, notes) VALUES ('src_empty', 'document', 'Empty Doc', '2026-10-26', '')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, object_literal, primary_source_id, currentness_state, canonical_status, validation_state) "
            "VALUES ('asrt_boiler_abcdef', 'stakeholder_dan_okray', 'HAS_CLAIM_TEXT', 'Daniel Okray is an identified stakeholder.', 'Daniel Okray is an identified stakeholder.', 'src_empty', 'current', 'canonical', 'accepted_legacy')"
        )
        resolved = _find_subject_node_id(conn, "Danny O")
        assert resolved == "stakeholder_dan_okray"

        stats = promote_all_candidates_and_empty_sources(conn, "2026-10-28T00:00:00Z")
        assert stats["empty_sources_backfilled"] == 0

        updated = remediate_boilerplate_assertions(conn, "2026-10-28T00:00:00Z")
        assert updated == 1
        new_scope = conn.execute("SELECT scope_text FROM assertion WHERE assertion_id = 'asrt_boiler_abcdef'").fetchone()[0]
        assert "[abcdef]" not in new_scope
    finally:
        conn.close()

    with pytest.raises(ValueError, match="Cannot persist role for empty"):
        persist_validated_role(db_path, "   ", "VP")

    prod_target = _REPO_ROOT / "graph" / "nee_graph.sqlite"
    with pytest.raises(ProductionPurityViolationError):
        GovernedStorageContext.connect(prod_target, readonly=False)
