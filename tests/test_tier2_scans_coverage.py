"""Comprehensive unit and coverage tests for Tier 2 Scans (Scan 0 through Scan 5) and Graph GC."""
from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tools.graph_gc import run_gc
from tools.tier2_common import dict_row_factory
from tools.tier2_scan0_baseline_stats import collect_baseline_stats
from tools.tier2_scan1_node_clusters import scan_node_clusters
from tools.tier2_scan2_predicate_synonyms import run_predicate_synonym_scan
from tools.tier2_scan3_assertion_overlaps import scan_assertion_overlaps
from tools.tier2_scan4_evidence_dedup import scan_evidence_fragmentation
from tools.tier2_scan5_lineage_gaps import scan_lineage_gaps

ROOT = Path(__file__).resolve().parents[1]
PROD_GRAPH = ROOT / "graph" / "nee_graph.sqlite"


def _h(s: str) -> str:
    """Return 64-char sha256 hex string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _create_full_schema_test_db(db_path: Path) -> sqlite3.Connection:
    """Initialize a SQLite database with all tables from the production graph schema."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = dict_row_factory

    if PROD_GRAPH.exists():
        src = sqlite3.connect(str(PROD_GRAPH))
        for row in src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
            with contextlib.suppress(Exception):
                conn.execute(row[0])
        src.close()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            stale_after TEXT,
            decision_text TEXT
        );
        CREATE TABLE IF NOT EXISTS risks (
            risk_id TEXT PRIMARY KEY,
            stale_after TEXT,
            risk_text TEXT
        );
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id TEXT PRIMARY KEY,
            stale_after TEXT,
            proposal_text TEXT
        );
        CREATE TABLE IF NOT EXISTS legacy_predicate_mapping (
            legacy_predicate TEXT PRIMARY KEY,
            target_predicate TEXT
        );
    """)
    conn.commit()
    return conn


def _insert_node(
    conn: sqlite3.Connection,
    node_id: str,
    canonical_name: str,
    node_type: str = "system",
    display_name: str | None = None,
    active: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO node (node_id, canonical_name, display_name, node_type, active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, '2026-09-27T00:00:00Z', '2026-09-27T00:00:00Z')""",
        (node_id, canonical_name, display_name or canonical_name, node_type, active),
    )


def _insert_source(
    conn: sqlite3.Connection,
    source_id: str,
    source_name: str = "Test Source",
    source_type: str = "meeting",
    authority_rank: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO source (source_id, source_name, source_type, authority_rank, source_posture, created_at)
           VALUES (?, ?, ?, ?, 'authoritative', '2026-09-27T00:00:00Z')""",
        (source_id, source_name, source_type, authority_rank),
    )


def _insert_source_version(
    conn: sqlite3.Connection,
    source_version_id: str,
    source_id: str,
) -> None:
    conn.execute(
        """INSERT INTO source_version (
            source_version_id, source_id, version_number, source_fingerprint,
            content_hash, source_date_precision, source_date_status, authority_rank,
            scope_text, posture_text, created_at
        ) VALUES (?, ?, 1, ?, ?, 'day', 'exact', 1, 'scope', 'posture', '2026-09-27T00:00:00Z')""",
        (source_version_id, source_id, _h(f"fp:{source_version_id}"), _h(f"content:{source_version_id}")),
    )


def _insert_evidence(
    conn: sqlite3.Connection,
    evidence_id: str,
    source_id: str,
    source_version_id: str,
    direct_quote: str,
    locator_value: str,
    fp_seed: str,
) -> None:
    conn.execute(
        """INSERT INTO evidence_item (
            evidence_id, evidence_fingerprint, source_id, source_version_id,
            locator_kind, locator_value, direct_quote, evidence_directness,
            source_authority_rank, extraction_confidence, location_status, created_at
        ) VALUES (?, ?, ?, ?, 'paragraph', ?, ?, 'direct', 1, 0.95, 'exact', '2026-09-27T00:00:00Z')""",
        (evidence_id, _h(fp_seed), source_id, source_version_id, locator_value, direct_quote),
    )


def _insert_assertion(
    conn: sqlite3.Connection,
    assertion_id: str,
    subject_node_id: str,
    predicate: str,
    object_node_id: str | None = None,
    object_literal: str | None = None,
    primary_source_id: str | None = None,
    canonical_status: str = "canonical",
    currentness_state: str = "current",
    confidence_score: float = 0.95,
    extraction_run_id: str | None = None,
) -> None:
    if object_node_id is None and object_literal is None:
        object_literal = "Default fact statement"
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, assertion_fingerprint, subject_node_id, predicate,
            object_node_id, object_literal, primary_source_id, extraction_run_id, assertion_type,
            atomicity_state, scope_text, extraction_method, origin_type,
            confidence_label, confidence_score, validation_state, currentness_state,
            date_precision, date_status, recorded_at, resolution_state,
            canonical_status, canonical_eligible, retrieval_eligible, materiality,
            classification, allowed_audiences_json, hitl_required, qualifiers_json,
            legacy_record_type, legacy_record_ids_json, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, 'claim', 'atomic', 'scope', 'deterministic', 'extracted',
            'high', ?, 'validated', ?, 'day', 'exact', '2026-09-27T00:00:00Z', 'resolved',
            ?, 1, 1, 'high', 'internal', '[]', 0, '{}', 'claim', '[]',
            '2026-09-27T00:00:00Z', '2026-09-27T00:00:00Z'
        )""",
        (
            assertion_id, _h(f"asrt:{assertion_id}"), subject_node_id, predicate,
            object_node_id, object_literal, primary_source_id, extraction_run_id,
            confidence_score, currentness_state, canonical_status,
        ),
    )


def _insert_canonical_alias(
    conn: sqlite3.Connection,
    canonical_alias_id: str,
    entity_id: str,
    alias: str,
    ambiguity_state: str = "unique",
) -> None:
    conn.execute(
        """INSERT INTO canonical_alias (
            canonical_alias_id, normalized_alias, display_alias, entity_id,
            alias_type, source_id, confidence_label, confidence_score,
            ambiguity_state, preferred_flag, review_state, active, created_at
        ) VALUES (?, ?, ?, ?, 'display_name', 'src:1', 'high', 0.95, ?, 1, 'reviewed', 1, '2026-09-27T00:00:00Z')""",
        (canonical_alias_id, alias.lower(), alias, entity_id, ambiguity_state),
    )


def _insert_edge_type(conn: sqlite3.Connection, edge_type_id: str, canonical_predicate: str) -> None:
    conn.execute(
        """INSERT INTO edge_type_registry (
            edge_type_id, canonical_predicate, definition, relationship_family,
            direction, symmetric_flag, transitive_flag, cardinality,
            temporal_requirement, evidence_required, qualifier_rules_json,
            registry_state, allow_self_loop, created_at
        ) VALUES (?, ?, 'Definition', 'collaborative', 'symmetric', 1, 0, 'many_to_many', 'not_required', 1, '{}', 'active', 0, '2026-09-27T00:00:00Z')""",
        (edge_type_id, canonical_predicate),
    )


def _insert_assertion_predicate(conn: sqlite3.Connection, predicate: str, definition: str = "Definition") -> None:
    conn.execute(
        """INSERT INTO assertion_predicate_registry (
            predicate, object_kind, cardinality, evidence_required, temporal_requirement,
            symmetric_mode, acyclic_flag, conflict_policy, active, definition, created_at
        ) VALUES (?, 'entity', 'many_to_many', 1, 'optional', 'always', 0, 'explicit_only', 1, ?, '2026-09-27T00:00:00Z')""",
        (predicate, definition),
    )


def _insert_edge(
    conn: sqlite3.Connection,
    edge_id: str,
    from_node_id: str,
    to_node_id: str,
    edge_type: str,
    source_id: str = "src:1",
    superseded_by: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO edge (
            edge_id, from_node_id, edge_type, to_node_id, evidence_posture,
            confidence, materiality, hitl_required, source_id, created_at, updated_at, superseded_by
        ) VALUES (?, ?, ?, ?, 'direct', 'high', 'high', 0, ?, '2026-09-27T00:00:00Z', '2026-09-27T00:00:00Z', ?)""",
        (edge_id, from_node_id, edge_type, to_node_id, source_id, superseded_by),
    )


def _insert_node_state(conn: sqlite3.Connection, node_id: str, intentional_orphan: int = 0) -> None:
    conn.execute(
        """INSERT INTO node_state (
            node_id, lifecycle_state, validation_state, currentness_state,
            approval_state, rag_state, resolution_state, retrieval_eligibility,
            publication_state, review_state, intentional_orphan, updated_at
        ) VALUES (?, 'active', 'validated', 'current', 'approved', 'indexed', 'resolved', 'eligible', 'published', 'reviewed', ?, '2026-09-27T00:00:00Z')""",
        (node_id, intentional_orphan),
    )


class Tier2ScansCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tier2-scans-")
        self.db_path = Path(self.temp_dir.name) / "test_graph.sqlite"
        self.conn = _create_full_schema_test_db(self.db_path)
        _insert_source(self.conn, "src:1", "Briefing Notes", "meeting")
        _insert_source_version(self.conn, "ver:1", "src:1")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_scan0_baseline_stats(self) -> None:
        _insert_node(self.conn, "node:1", "Nexus Core", "system", active=1)
        _insert_assertion(self.conn, "asrt:1", "node:1", "HAS_STATUS", canonical_status="canonical", currentness_state="current")
        self.conn.commit()

        stats = collect_baseline_stats(self.conn)
        self.assertIn("table_counts", stats)
        self.assertEqual(stats["table_counts"]["node"]["row_count"], 1)
        self.assertEqual(stats["table_counts"]["source"]["row_count"], 1)
        self.assertEqual(stats["table_counts"]["assertion"]["row_count"], 1)

    def test_scan1_node_clusters(self) -> None:
        _insert_node(self.conn, "org:partner_advisory", "PartnerAdvisory Consulting", "organization", display_name="PartnerAdvisory")
        _insert_node(self.conn, "org:partner_advisory_inc", "PartnerAdvisory Consulting LLC", "organization", display_name="PartnerAdvisory Inc")
        _insert_canonical_alias(self.conn, "alias:1", "org:partner_advisory", "PartnerAdvisory", ambiguity_state="ambiguous")
        _insert_canonical_alias(self.conn, "alias:2", "org:partner_advisory_inc", "PartnerAdvisory", ambiguity_state="ambiguous")
        self.conn.commit()

        clusters = scan_node_clusters(self.conn)
        self.assertIn("same_type_clusters", clusters)
        self.assertIn("ambiguous_aliases", clusters)
        self.assertGreaterEqual(len(clusters["ambiguous_aliases"]), 1)

    def test_scan2_predicate_synonyms(self) -> None:
        _insert_edge_type(self.conn, "PARTNERS_WITH", "PARTNERS_WITH")
        _insert_assertion_predicate(self.conn, "PARTNERS_WITH", "Partners")
        _insert_assertion_predicate(self.conn, "COLLABORATES_WITH", "Collaborates")
        self.conn.commit()

        res = run_predicate_synonym_scan(self.conn)
        self.assertIn("total_active_predicates", res)
        self.assertGreaterEqual(res["total_active_predicates"], 2)

    def test_scan3_assertion_overlaps(self) -> None:
        _insert_node(self.conn, "proj:1", "Project Nexus", "project")
        _insert_node(self.conn, "status:ready", "Ready", "status")
        _insert_assertion(self.conn, "asrt:101", "proj:1", "HAS_STATUS", object_node_id="status:ready", primary_source_id="src:1", confidence_score=0.95)
        _insert_assertion(self.conn, "asrt:102", "proj:1", "HAS_STATUS", object_node_id="status:ready", primary_source_id="src:1", confidence_score=0.80)
        self.conn.commit()

        overlaps = scan_assertion_overlaps(self.conn)
        self.assertIn("object_node_duplicates", overlaps)
        self.assertGreaterEqual(len(overlaps["object_node_duplicates"]), 1)

    def test_scan4_evidence_dedup(self) -> None:
        _insert_evidence(self.conn, "ev:1", "src:1", "ver:1", "Delivery in October 2026.", "p:1", "seed:1")
        _insert_evidence(self.conn, "ev:2", "src:1", "ver:1", "Delivery in October 2026.", "p:1", "seed:2")
        self.conn.commit()

        frag = scan_evidence_fragmentation(self.conn)
        self.assertIn("locator_duplicates", frag)
        self.assertGreaterEqual(len(frag["locator_duplicates"]), 1)
        self.assertIn("quote_duplicates", frag)
        self.assertGreaterEqual(len(frag["quote_duplicates"]), 1)

    def test_scan5_lineage_gaps(self) -> None:
        _insert_node(self.conn, "p:1", "Person 1", "person")
        _insert_node(self.conn, "p:2", "Person 2", "person")
        _insert_edge(self.conn, "edge:1", "p:1", "p:2", "UNKNOWN_TYPE")
        _insert_assertion(self.conn, "asrt:gap", "p:1", "HAS_ROLE")
        self.conn.commit()

        gaps = scan_lineage_gaps(self.db_path)
        self.assertIn("no_extraction_run", gaps)
        self.assertGreaterEqual(len(gaps["no_extraction_run"]), 1)

    def test_graph_gc_stale_and_orphan_pruning(self) -> None:
        _insert_node(self.conn, "node:connected", "Connected Node", "system")
        _insert_assertion(self.conn, "asrt:live", "node:connected", "HAS_STATUS")

        _insert_node(self.conn, "node:orphan", "Dangling Node", "system")
        _insert_node_state(self.conn, "node:orphan", intentional_orphan=0)

        _insert_node(self.conn, "node:intentional", "Intentional Root", "system")
        _insert_node_state(self.conn, "node:intentional", intentional_orphan=1)

        self.conn.execute("INSERT INTO decisions (decision_id, stale_after, decision_text) VALUES ('dec:stale', '2020-01-01', 'Old decision')")
        self.conn.commit()

        # 1. Dry Run
        dry_res = run_gc(self.db_path, dry_run=True, as_of=date(2026, 1, 1), prune_orphans=True)
        self.assertEqual(dry_res["status"], "dry_run")
        self.assertIn("node:orphan", dry_res["would_archive"]["orphan_nodes"])
        self.assertNotIn("node:intentional", dry_res["would_archive"].get("orphan_nodes", []))
        self.assertIn("dec:stale", dry_res["would_archive"]["decisions"])

        # 2. Live Execution
        exec_res = run_gc(self.db_path, dry_run=False, as_of=date(2026, 1, 1), prune_orphans=True)
        self.assertEqual(exec_res["status"], "archived")
        self.assertEqual(exec_res["archived_counts"]["orphan_nodes"], 1)
        self.assertEqual(exec_res["archived_counts"]["decisions"], 1)

        cur = self.conn.cursor()
        self.assertIsNone(cur.execute("SELECT 1 FROM node WHERE node_id='node:orphan'").fetchone())
        self.assertIsNotNone(cur.execute("SELECT 1 FROM node WHERE node_id='node:intentional'").fetchone())
        self.assertIsNotNone(cur.execute("SELECT 1 FROM node WHERE node_id='node:connected'").fetchone())
        self.assertIsNone(cur.execute("SELECT 1 FROM decisions WHERE decision_id='dec:stale'").fetchone())

        self.assertIsNotNone(cur.execute("SELECT 1 FROM archived_node WHERE node_id='node:orphan'").fetchone())
        self.assertIsNotNone(cur.execute("SELECT 1 FROM archived_decisions WHERE decision_id='dec:stale'").fetchone())


if __name__ == "__main__":
    unittest.main()
