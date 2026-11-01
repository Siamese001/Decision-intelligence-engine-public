"""Adversarial and edge-case tests for October 27-28 retrieval effectiveness hygiene scanners."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dual_pipeline.graph_scout_core.antipattern_scans import scan_retrieval_antipatterns
from storage.database import sqlite3
from tools.tier2_dedup_core.sidecar_vector_recovery import remediate_additional_hygiene_fixes


def test_boundary_empty_database_and_missing_tables(tmp_path: Path) -> None:
    """Category 1 (Boundary payload): 0-table and empty-table databases return [] without error."""
    db_path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(str(db_path))
    assert scan_retrieval_antipatterns(conn) == []

    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY)")
    conn.commit()
    assert scan_retrieval_antipatterns(conn) == []
    conn.close()


def test_malformed_qualifiers_json_and_null_fields(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Truncated JSON and NULL text columns do not crash scanners."""
    db_path = tmp_path / "malformed.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            description TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            qualifiers_json TEXT,
            superseded_by_assertion_id TEXT
        );
        INSERT INTO node VALUES ('person_1', 'person', NULL, NULL, 1);
        INSERT INTO assertion VALUES (
            'asrt_bad_json', 'person_1', 'HAS_CLAIM_TEXT', NULL, NULL, NULL,
            'canonical', 'current', 1, '{truncated_json', NULL
        );
        """
    )
    conn.commit()
    findings = scan_retrieval_antipatterns(conn)
    assert isinstance(findings, list)
    conn.close()


def test_read_only_invariant_enforcement_during_scan(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass attempt): Scanners must never write under PRAGMA query_only = ON."""
    db_path = tmp_path / "readonly.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            description TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            qualifiers_json TEXT,
            superseded_by_assertion_id TEXT
        );
        INSERT INTO node VALUES ('person_brien', 'person', 'Speaker_Alpha Muschett', 'Exact title remain unresolved', 1);
        INSERT INTO node VALUES ('role_ea', 'role', 'Enterprise Architect', 'EA', 1);
        INSERT INTO assertion VALUES (
            'a1', 'person_brien', 'HAS_ROLE_IN', 'role_ea', NULL, 'Enterprise Architect',
            'canonical', 'current', 1, '{}', NULL
        );
        """
    )
    conn.commit()
    conn.execute("PRAGMA query_only = ON")
    findings = scan_retrieval_antipatterns(conn)
    assert any(f.action_recommended == "reconcile_stale_role_and_description" for f in findings)
    conn.close()


def test_concurrent_read_scans_and_idempotent_remediation(tmp_path: Path) -> None:
    """Categories 3 & 6 (Concurrency & Idempotent recovery): Parallel scans and repeated remediation."""
    db_path = tmp_path / "concurrent.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            description TEXT,
            active INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            validation_state TEXT DEFAULT 'verified',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            is_quarantined INTEGER DEFAULT 0,
            qualifiers_json TEXT DEFAULT '{}',
            superseded_by_assertion_id TEXT,
            updated_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            assertion_id TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            updated_at TEXT
        );
        INSERT INTO node VALUES ('person_tyler', 'person', 'Tyler McCord', '100% operations (onboarding, access, laptops)', 1, NULL);
        INSERT INTO node VALUES ('workstream_project_beacon', 'workstream', 'Project Beacon', 'Program', 1, NULL);
        INSERT INTO assertion VALUES (
            'asrt_tyler', 'person_tyler', 'AFFILIATED_WITH', 'workstream_project_beacon',
            'Tyler McCord operations', 'Tyler McCord 100% operations on Project Beacon',
            'canonical', 'current', 'verified', 1, 1, 0,
            '{"boundary_type": "disjoint_workstream_exclusion"}', NULL, NULL
        );
        INSERT INTO search_document VALUES ('sd_tyler', 'asrt_tyler', 'canonical', 'current', 1, NULL);
        """
    )
    conn.commit()
    conn.close()

    def _worker() -> int:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return len(scan_retrieval_antipatterns(c))
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        counts = list(pool.map(lambda _: _worker(), range(8)))
    assert all(c >= 1 for c in counts)

    wconn = sqlite3.connect(str(db_path))
    pkg = {
        "findings": [
            {
                "proposed_fix": "quarantine_disjoint_role_or_owner_bleed",
                "entity_references": ["asrt_tyler"],
                "raw_data": {"assertion_id": "asrt_tyler"},
            }
        ]
    }
    sample_ids: list[str] = []
    res1 = remediate_additional_hygiene_fixes(wconn, pkg, "2026-10-29T06:00:00Z", sample_ids)
    wconn.commit()
    res2 = remediate_additional_hygiene_fixes(wconn, pkg, "2026-10-29T06:00:00Z", sample_ids)
    wconn.commit()
    assert res1["ungrounded_assertions_reconciled"] + res1["revalidation_90d_archived"] >= 1
    assert res2["revalidation_90d_archived"] >= 0
    assert scan_retrieval_antipatterns(wconn) == []
    wconn.close()
