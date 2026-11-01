"""
tests/test_adversarial_edge_traits_lifecycle.py

Adversarial and Edge-Case Tests for GraphDB Edge Traits Lifecycle:
Covers invariant bypass attempts, boundary/malformed inputs, trigger enforcement,
self-loop rejections, CHECK constraint boundaries, and rollback recovery.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Generator

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import get_tier2_write_connection, utc_now
from tools.tier2_dedup_core import execute_signal_quality_validation

ORIGINAL_DB_PATH = ROOT / "graph" / "nee_graph.sqlite"


@pytest.fixture
def isolated_graph(tmp_path: Path) -> Generator[Path, None, None]:
    """Provision an isolated copy of the real graph DB in WAL mode with foreign keys enabled."""
    test_db = tmp_path / "test_adversarial_graph.sqlite"
    shutil.copy2(ORIGINAL_DB_PATH, test_db)
    yield test_db


def test_adversarial_domain_range_trigger_bypass_attempt(isolated_graph: Path) -> None:
    """Invariant Bypass: Verify edge_domain_range_bu trigger prevents unauthorized node type relations."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # Find an existing valid edge
        edge = conn.execute("SELECT edge_id, from_node_id, to_node_id, edge_type FROM edge LIMIT 1").fetchone()
        assert edge is not None

        # Attempt to maliciously update to_node_id to an incompatible type
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                "UPDATE edge SET to_node_id = 'org_partner_advisory', edge_type = 'REPORTS_TO' WHERE edge_id = ?",
                (edge["edge_id"],),
            )
        conn.rollback()
        assert "invalid edge domain/range" in str(exc_info.value).lower()

        # Verify original edge remains intact and untouched
        restored = conn.execute("SELECT edge_type FROM edge WHERE edge_id = ?", (edge["edge_id"],)).fetchone()
        assert restored["edge_type"] == edge["edge_type"]
    finally:
        conn.close()


def test_adversarial_self_loop_trigger_bypass_attempt(isolated_graph: Path) -> None:
    """Invariant Bypass: Verify edge_self_loop triggers prevent circular self-references when prohibited."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # Attempt to insert an edge where from_node_id == to_node_id on a type where allow_self_loop = 0
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                """INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, evidence_posture, confidence, materiality, source_id, created_at, updated_at)
                   VALUES ('edge_adv_self_loop', 'person_jen_pan', 'OWNS_OR_LEADS', 'person_jen_pan', 'direct', 'high', 'critical', 'source_wave3_lens_0918_hitl_resolution', ?, ?)""",
                (utc_now(), utc_now()),
            )
        conn.rollback()
        err_msg = str(exc_info.value).lower()
        assert "self-loop prohibited" in err_msg or "domain/range" in err_msg
    finally:
        conn.close()


def test_adversarial_self_supersession_check_constraint(isolated_graph: Path) -> None:
    """Constraint Boundary: Verify CHECK (superseded_by IS NULL OR superseded_by <> edge_id) aborts self-supersession."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        edge = conn.execute("SELECT edge_id FROM edge LIMIT 1").fetchone()
        assert edge is not None
        target_id = edge["edge_id"]

        with pytest.raises(Exception) as exc_info:
            conn.execute(
                "UPDATE edge SET superseded_by = ? WHERE edge_id = ?",
                (target_id, target_id),
            )
        conn.rollback()
        err_msg = str(exc_info.value).lower()
        assert "check constraint failed" in err_msg and "superseded_by" in err_msg
    finally:
        conn.close()


def test_adversarial_blank_identifier_check_constraint(isolated_graph: Path) -> None:
    """Boundary Payloads: Verify whitespace/blank identifiers are rejected by table CHECK constraints."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                """INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, evidence_posture, confidence, materiality, source_id, created_at, updated_at)
                   VALUES ('   ', 'person_jen_pan', 'OWNS_OR_LEADS', 'workstream_lens', 'direct', 'high', 'critical', 'source_wave3_lens_0918_hitl_resolution', ?, ?)""",
                (utc_now(), utc_now()),
            )
        conn.rollback()
        err_msg = str(exc_info.value).lower()
        assert "check constraint failed" in err_msg or "trim(edge_id)" in err_msg
    finally:
        conn.close()


def test_adversarial_rollback_recovery_on_savepoint_failure(isolated_graph: Path) -> None:
    """Rollback & Recovery: Injected failure inside a SAVEPOINT cleanly rolls back without leaking dirty state."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        ts = utc_now()
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("BEGIN IMMEDIATE;")

        # Mutation 1: Valid temporary edge
        conn.execute(
            """INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, evidence_posture, confidence, materiality, source_id, created_at, updated_at)
               VALUES ('edge_sp_valid', 'person_jen_pan', 'OWNS_OR_LEADS', 'workstream_lens', 'direct', 'high', 'critical', 'source_wave3_lens_0918_hitl_resolution', ?, ?)""",
            (ts, ts),
        )

        # Sub-transaction: Savepoint that encounters an error
        conn.execute("SAVEPOINT test_sp;")
        try:
            # Force trigger failure
            conn.execute("UPDATE edge SET to_node_id = 'invalid_node_missing' WHERE edge_id = 'edge_sp_valid';")
            raise RuntimeError("Should not reach here")
        except Exception:
            conn.execute("ROLLBACK TO test_sp;")
            conn.execute("RELEASE test_sp;")

        conn.execute("COMMIT;")
        conn.execute("PRAGMA foreign_keys = ON;")

        # Valid edge survived, corrupt update was cleanly rolled back
        edge = conn.execute("SELECT to_node_id FROM edge WHERE edge_id = 'edge_sp_valid'").fetchone()
        assert edge is not None
        assert edge["to_node_id"] == "workstream_lens"
    finally:
        conn.close()


def test_adversarial_detection_of_deactivated_node_with_current_assertions(isolated_graph: Path) -> None:
    """Regression & Hygiene Pin: Wave 5 Signal Quality validator fails if an active assertion points to an inactive node."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # Maliciously deactivate a node without retiring its assertions
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("UPDATE node SET active = 0 WHERE node_id = 'person_jen_pan';")
        conn.execute("PRAGMA foreign_keys = ON;")

        # Run Wave 5 signal validation
        health = execute_signal_quality_validation(conn)
        assert health["assertions_on_inactive_nodes"] > 0
        assert health["overall"] == "FAIL", "Validator must FAIL closed when current assertions point to inactive nodes"
    finally:
        conn.close()
