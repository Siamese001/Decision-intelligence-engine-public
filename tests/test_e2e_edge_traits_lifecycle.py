"""
tests/test_e2e_edge_traits_lifecycle.py

End-to-End Integration Tests for GraphDB Edge Traits Lifecycle:
1. ASSERTION TYPE (Semantic predicate meaning, ontology constraints, domain/range, and consolidation)
2. CONFIDENCE (Trust calibration, Bayesian corroboration boost, and supersession)
3. EVIDENCE (Verbatim grounding, 100% provenance coverage, and non-destructive transfer)
4. LINEAGE (Cryptographic source chain, lineage gap repair, audit trail, and hash transition)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Generator

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tier2_common import (
    get_tier2_write_connection,
    sha256_file,
    stable_id,
    utc_now,
)
from tools.tier2_dedup_core import (
    execute_assertion_supersessions,
    execute_edge_lineage_fixes,
    execute_node_merges,
    execute_predicate_consolidation,
    execute_search_and_fts_rebuild,
    execute_signal_quality_validation,
)

ORIGINAL_DB_PATH = ROOT / "graph" / "nee_graph.sqlite"


@pytest.fixture
def isolated_graph(tmp_path: Path) -> Generator[Path, None, None]:
    """Provision an isolated copy of the real graph DB in WAL mode with foreign keys enabled."""
    test_db = tmp_path / "test_nee_graph.sqlite"
    shutil.copy2(ORIGINAL_DB_PATH, test_db)
    yield test_db


def _record_audit(
    conn: Any,
    action: str,
    record_type: str,
    record_id: str,
    prev: dict | None,
    new: dict | None,
    reason: str,
) -> None:
    event_id = stable_id("audit", action, record_id, utc_now())
    conn.execute(
        """INSERT INTO audit_event (
            audit_event_id, actor_or_process, action, record_type,
            record_id, previous_value_json, new_value_json,
            event_timestamp, reason, release_version,
            migration_or_ingestion_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            "test_e2e_edge_traits",
            action,
            record_type,
            record_id,
            json.dumps(prev) if prev else None,
            json.dumps(new) if new else None,
            utc_now(),
            reason,
            "e2e-traits-v1",
            "run-e2e-traits",
        ),
    )


def test_e2e_trait1_assertion_type_meaning_and_rules(isolated_graph: Path) -> None:
    """Trait 1: ASSERTION TYPE (Semantic predicate meaning, domain/range triggers, and normalization)."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # A. Trigger rejects invalid node type pairing for edge
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                """INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, evidence_posture, confidence, materiality, source_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'direct', 'high', 'critical', 'source_wave3_lens_0918_hitl_resolution', ?, ?)""",
                ("edge_invalid_type_test", "workstream_rewire", "REPORTS_TO", "person_jen_pan", utc_now(), utc_now()),
            )
        conn.rollback()
        assert "invalid edge domain/range" in str(exc_info.value).lower()

        # B. Trigger rejects self-loop edge when allow_self_loop = 0
        with pytest.raises(Exception) as loop_exc:
            conn.execute(
                """INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, evidence_posture, confidence, materiality, source_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'direct', 'high', 'critical', 'source_wave3_lens_0918_hitl_resolution', ?, ?)""",
                ("edge_invalid_self_loop", "org_partner_advisory", "PARTNERS_WITH", "org_partner_advisory", utc_now(), utc_now()),
            )
        conn.rollback()
        assert "self-loop prohibited" in str(loop_exc.value).lower() or "domain/range" in str(loop_exc.value).lower()

        # C. Execute predicate consolidation (Wave 3b logic)
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("BEGIN IMMEDIATE;")
        stats = execute_predicate_consolidation(conn, _record_audit)
        conn.execute("COMMIT;")
        conn.execute("PRAGMA foreign_keys = ON;")

        assert stats["predicates_remapped"] == 6
        assert stats["edges_remapped"] == 6
        assert len(stats["predicate_skips"]) == 0

        # D. Verify BUILDS was consolidated to canonical DELIVERS
        builds_edge = conn.execute(
            "SELECT edge_type, updated_at FROM edge WHERE edge_id = 'edge_engineering_exchange_v20_builds_pattern_library'"
        ).fetchone()
        assert builds_edge is not None
        assert builds_edge["edge_type"] == "DELIVERS"

        # E. Verify edge_type_registry deprecation & replacement pointer
        reg_row = conn.execute(
            "SELECT registry_state, replacement_predicate FROM edge_type_registry WHERE edge_type_id = 'BUILDS'"
        ).fetchone()
        assert reg_row["registry_state"] == "deprecated"
        assert reg_row["replacement_predicate"] == "DELIVERS"

        # F. Verify domain/range entries were migrated for DELIVERS
        dr = conn.execute(
            "SELECT * FROM edge_type_domain_range WHERE edge_type_id = 'DELIVERS' AND source_node_type = 'pattern' AND target_node_type = 'pattern'"
        ).fetchone()
        assert dr is not None
        assert dr["active"] == 1
    finally:
        conn.close()


def test_e2e_trait2_confidence_trust_and_supersession(isolated_graph: Path) -> None:
    """Trait 2: CONFIDENCE (Trust calibration, corroboration boost, and decisive supersession)."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # A. Inspect pre-supersession competing assertions on same fact
        competing = conn.execute(
            """SELECT a1.assertion_id AS id_a, a1.confidence_score AS conf_a,
                      a2.assertion_id AS id_b, a2.confidence_score AS conf_b,
                      a1.subject_node_id, a1.predicate
               FROM assertion a1
               JOIN assertion a2 ON a1.subject_node_id = a2.subject_node_id
                                AND a1.predicate = a2.predicate
                                AND a1.object_node_id = a2.object_node_id
                                AND a1.assertion_id < a2.assertion_id
               WHERE a1.canonical_status = 'canonical' AND a2.canonical_status = 'canonical'
                 AND a1.currentness_state = 'current' AND a2.currentness_state = 'current'
                 AND a1.confidence_score != a2.confidence_score
               LIMIT 1"""
        ).fetchone()
        assert competing is not None
        winner_id = competing["id_a"] if competing["conf_a"] > competing["conf_b"] else competing["id_b"]
        loser_id = competing["id_b"] if competing["conf_a"] > competing["conf_b"] else competing["id_a"]
        delta = abs(competing["conf_a"] - competing["conf_b"])
        assert delta >= 0.05

        # B. Execute assertion supersessions
        conn.execute("BEGIN IMMEDIATE;")
        count, supersessions = execute_assertion_supersessions(conn, _record_audit)
        conn.execute("COMMIT;")

        assert count >= 30
        assert any(s["winner"] == winner_id and s["loser"] == loser_id for s in supersessions)

        # C. Verify loser is retired to historical with pointer to winner
        loser_rec = conn.execute(
            "SELECT currentness_state, canonical_status, superseded_by_assertion_id FROM assertion WHERE assertion_id = ?",
            (loser_id,),
        ).fetchone()
        assert loser_rec["currentness_state"] == "historical"
        assert loser_rec["canonical_status"] == "historical"
        assert loser_rec["superseded_by_assertion_id"] == winner_id

        # D. Verify winner remains canonical and current
        winner_rec = conn.execute(
            "SELECT currentness_state, canonical_status, superseded_by_assertion_id FROM assertion WHERE assertion_id = ?",
            (winner_id,),
        ).fetchone()
        assert winner_rec["currentness_state"] == "current"
        assert winner_rec["canonical_status"] == "canonical"
        assert winner_rec["superseded_by_assertion_id"] is None
    finally:
        conn.close()


def test_e2e_trait3_evidence_sticky_notes_and_consolidation(isolated_graph: Path) -> None:
    """Trait 3: EVIDENCE (Verbatim grounding, provenance invariant, and non-destructive transfer)."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # A. Verify 100% provenance coverage gate before any mutation
        evidence_gap = conn.execute(
            """SELECT COUNT(*) FROM assertion a
               WHERE a.canonical_status = 'canonical' AND a.currentness_state = 'current'
                 AND a.superseded_by_assertion_id IS NULL
                 AND NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id)"""
        ).fetchone()[0]
        assert evidence_gap == 0, "All canonical current assertions must have evidence backing"

        # B. Record evidence count before merge
        total_evidence_before = conn.execute("SELECT COUNT(*) FROM evidence_item").fetchone()[0]
        assert total_evidence_before >= 1600

        # C. Execute node merges and supersessions
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("BEGIN IMMEDIATE;")
        merged_nodes, merge_events = execute_node_merges(conn, "test-evidence-transfer", _record_audit)
        superseded_count, supersessions = execute_assertion_supersessions(conn, _record_audit)
        conn.execute("COMMIT;")
        conn.execute("PRAGMA foreign_keys = ON;")

        assert merged_nodes == 5
        assert superseded_count >= 30

        # D. Non-destructive evidence invariant: evidence items were never deleted
        total_evidence_after = conn.execute("SELECT COUNT(*) FROM evidence_item").fetchone()[0]
        assert total_evidence_after == total_evidence_before

        # E. Evidence was consolidated onto surviving canonical assertions
        for event in supersessions:
            w_ev = conn.execute(
                "SELECT COUNT(*) FROM assertion_evidence WHERE assertion_id = ?",
                (event["winner"],),
            ).fetchone()[0]
            assert w_ev >= 1, f"Winner {event['winner']} must retain evidence links"
    finally:
        conn.close()


def test_e2e_trait4_lineage_source_provenance_and_audit(isolated_graph: Path) -> None:
    """Trait 4: LINEAGE (Cryptographic source chain, lineage repair, and immutable audit)."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        pre_sha = sha256_file(isolated_graph)

        # A. Verify complete 7-stage chain of custody
        chain_query = """
            SELECT s.source_id, sv.source_version_id, er.extraction_run_id,
                   ei.evidence_id, a.assertion_id, e.edge_id
            FROM source s
            JOIN source_version sv ON s.source_id = sv.source_id
            JOIN assertion a ON s.source_id = a.primary_source_id
            JOIN extraction_run er ON a.extraction_run_id = er.extraction_run_id
            JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
            JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id
            JOIN legacy_edge_assertion_map leam ON a.assertion_id = leam.assertion_id
            JOIN edge e ON leam.edge_id = e.edge_id
            LIMIT 1;
        """
        row = conn.execute(chain_query).fetchone()
        assert row is not None
        assert row["source_id"] and row["assertion_id"] and row["edge_id"]
        assert row["extraction_run_id"] and row["evidence_id"]

        # B. Verify unmigrated edge detection and lineage gap repair
        unmigrated_before = conn.execute(
            """SELECT COUNT(*) FROM edge e
               WHERE e.superseded_by IS NULL
                 AND e.edge_id NOT IN (SELECT edge_id FROM legacy_edge_assertion_map)"""
        ).fetchone()[0]
        assert unmigrated_before >= 1

        # Execute lineage repair
        conn.execute("BEGIN IMMEDIATE;")
        fixed = execute_edge_lineage_fixes(conn, _record_audit)
        conn.execute("COMMIT;")
        assert fixed >= 1

        unmigrated_after = conn.execute(
            """SELECT COUNT(*) FROM edge e
               WHERE e.superseded_by IS NULL
                 AND e.edge_id NOT IN (SELECT edge_id FROM legacy_edge_assertion_map)"""
        ).fetchone()[0]
        assert unmigrated_after == unmigrated_before - fixed

        # C. Verify immutable audit trail was emitted
        audit = conn.execute(
            "SELECT action, record_type, record_id, previous_value_json, new_value_json FROM audit_event WHERE action = 'edge_lineage_fix' LIMIT 1"
        ).fetchone()
        assert audit is not None
        assert audit["record_type"] == "edge"
        assert "migrated" in audit["new_value_json"]

        # D. Cryptographic state transition proof
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        post_sha = sha256_file(isolated_graph)
        assert pre_sha != post_sha, "Mutations must produce a deterministic cryptographic hash change"
    finally:
        conn.close()


def test_e2e_integrated_all_four_traits_lifecycle(isolated_graph: Path) -> None:
    """Full lifecycle integration verifying all 4 traits operate in unison with Wave 5 PASS."""
    conn = get_tier2_write_connection(isolated_graph)
    try:
        # Full Wave 2-5 lifecycle run
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("BEGIN IMMEDIATE;")
        execute_node_merges(conn, "run-e2e-all-traits", _record_audit)
        execute_assertion_supersessions(conn, _record_audit)
        execute_edge_lineage_fixes(conn, _record_audit)
        execute_predicate_consolidation(conn, _record_audit)
        execute_search_and_fts_rebuild(conn)
        conn.execute("COMMIT;")
        conn.execute("PRAGMA foreign_keys = ON;")

        # Terminal Wave 5 Health Verification Gate
        health = execute_signal_quality_validation(conn)
        assert health["overall"] == "PASS"
        assert health["integrity"] == "ok"
        assert health["duplicate_active_edges"] == 0
        assert health["assertions_on_inactive_nodes"] == 0
        assert health["stale_retrieval_docs"] == 0
        assert health["provenance_coverage"] >= 99.0
        assert health["final_counts"]["node_active"] == 642
        assert health["final_counts"]["active_edges"] == 1006
    finally:
        conn.close()
