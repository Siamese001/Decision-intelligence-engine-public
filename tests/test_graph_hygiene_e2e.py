"""
End-to-end tests for the Knowledge Graph Hygiene Panel (`GraphHygienePanel` and `GraphScout`).
Verifies that the full 5-agent adversarial hygiene pipeline produces zero false-positive
retirement or merge recommendations across historical edges, fresh candidate nodes,
evidence-backed candidate assertions, distinct roles/metrics/workstreams, and cross-meeting claims.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel


def _seed_production_like_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    cur.execute(
        """CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            intentional_orphan INTEGER DEFAULT 0
        )"""
    )
    cur.execute(
        """CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            superseded_by TEXT
        )"""
    )
    cur.execute(
        """CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT,
            assertion_id TEXT
        )"""
    )
    cur.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            validation_state TEXT,
            confidence_score REAL,
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT,
            valid_from TEXT,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
    cur.execute(
        """CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            direct_quote TEXT,
            locator_value TEXT,
            extraction_confidence REAL,
            created_at TEXT
        )"""
    )

    # 1. Consolidated historical node + superseded edge (must NOT be flagged as dangling_endpoint)
    cur.execute(
        "INSERT INTO node VALUES ('person:old_alias', 'person', 'Old Alias', 'Old', 'Merged record', 'merged', 0, '2026-06-01T00:00:00Z', '2026-10-03T00:00:00Z')"
    )
    cur.execute(
        "INSERT INTO node VALUES ('org:nea', 'org', 'Apex Global Analytics / NEA', 'NEA', 'Confirmed horizontal layer', 'active', 1, '2026-06-01T00:00:00Z', '2026-10-03T00:00:00Z')"
    )
    cur.execute(
        "INSERT INTO edge VALUES ('edge-hist-1', 'person:old_alias', 'org:nea', 'AFFILIATED_WITH', 'asrt-canon-1')"
    )
    cur.execute("INSERT INTO legacy_edge_assertion_map VALUES ('edge-hist-1', 'asrt-hist-1')")
    cur.execute(
        """INSERT INTO assertion VALUES (
            'asrt-hist-1', 'person:old_alias', 'AFFILIATED_WITH', 'org:nea', NULL, 'Historical link',
            'historical', 'historical', 'superseded', 0.90, 0, 'asrt-canon-1',
            '2026-06-01', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z', '2026-10-03T00:00:00Z'
        )"""
    )

    # 2. Newly created candidate node (must NOT be flagged as orphan_node)
    cur.execute(
        "INSERT INTO node VALUES ('entity-fresh-cand', 'workstream', 'Workstream B – PRF Integration', 'WS B', 'Hands-on development', 'candidate', 1, datetime('now', '-1 day'), datetime('now', '-1 day'))"
    )
    cur.execute("INSERT INTO node_state VALUES ('entity-fresh-cand', 0)")

    # 3. Distinct entity pairs that must NEVER be merged
    distinct_nodes = [
        ("pattern:cap28", "pattern", "Estimated 2028 capital value", "$6.92M Capital estimated value by 2028."),
        ("pattern:tot28", "pattern", "Estimated 2028 total value", "$11.11M total estimated value by 2028."),
        ("ws:aisdlc", "workstream", "AI-SDLC", "Engineering-velocity workstream"),
        ("ws:aidlc", "workstream", "AI-DLC", "Governance and evaluation workstream"),
        ("sh:mvaldes", "stakeholder", "Michael Valdes", "Enterprise Cloud Lead"),
        ("sh:mvaldes_admin", "stakeholder", "Michael Valdes Admin", "IT Systems Administrator"),
        ("sh:jacob", "stakeholder", "Jacob Wellington", "AI Software Engineering Leader"),
        ("unresolved_jake_wellington_roster_2026_07_11", "stakeholder", "Jake Wellington", "Do not merge with a similarly named canonical person without authoritative identity evidence."),
        ("rg:ea", "role_group", "Enterprise Architect", "Professional role: Enterprise Architect"),
        ("rg:lea", "role_group", "Lead Enterprise Architect", "Professional role: Lead Enterprise Architect"),
    ]
    for nid, ntype, cname, desc in distinct_nodes:
        cur.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')",
            (nid, ntype, cname, cname, desc),
        )
        cur.execute(
            """INSERT INTO assertion VALUES (
                ?, ?, 'HAS_DESCRIPTION', NULL, ?, ?,
                'canonical', 'current', 'approved', 0.95, 1, NULL,
                '2026-06-01', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
            )""",
            (f"asrt-{nid}", nid, desc, desc),
        )
        cur.execute("INSERT INTO evidence_item VALUES (?, ?, '00:01:00', 1.0, datetime('now', '-2 days'))", (f"ev-{nid}", desc))
        cur.execute("INSERT INTO assertion_evidence VALUES (?, ?)", (f"asrt-{nid}", f"ev-{nid}"))

    # 4. Cross-meeting assertions on different dates (must NOT be deduplicated)
    cur.execute(
        """INSERT INTO assertion VALUES (
            'asrt-mtg-1008', 'org:nea', 'IDENTIFIED_IN', NULL,
            'IBM Maximo is an identified technical_anchor in 20261008 Lens Sprint Review.',
            'IBM Maximo is an identified technical_anchor in 20261008 Lens Sprint Review.',
            'canonical', 'current', 'approved', 0.95, 1, NULL,
            '2026-10-08', '2026-10-08T00:00:00Z', '2026-10-08T00:00:00Z', '2026-10-08T00:00:00Z'
        )"""
    )
    cur.execute(
        """INSERT INTO assertion VALUES (
            'asrt-mtg-1005', 'org:nea', 'IDENTIFIED_IN', NULL,
            'IBM Maximo is an identified technical_anchor in 20261005 Lens Scope Review.',
            'IBM Maximo is an identified technical_anchor in 20261005 Lens Scope Review.',
            'canonical', 'current', 'approved', 0.95, 1, NULL,
            '2026-10-05', '2026-10-05T00:00:00Z', '2026-10-05T00:00:00Z', '2026-10-05T00:00:00Z'
        )"""
    )
    cur.execute("INSERT INTO assertion_evidence VALUES ('asrt-mtg-1008', 'ev-pattern:cap28')")
    cur.execute("INSERT INTO assertion_evidence VALUES ('asrt-mtg-1005', 'ev-pattern:tot28')")

    conn.commit()
    conn.close()


def test_graph_hygiene_e2e_zero_false_positives(tmp_path: Path) -> None:
    db_path = tmp_path / "e2e_hygiene.sqlite"
    _seed_production_like_graph(db_path)

    panel = GraphHygienePanel()
    pkg = panel.run_panel(db_path)

    fixes = [f.proposed_fix for f in pkg.findings]
    assert "archive_edge" not in fixes
    assert "archive_node" not in fixes
    assert "archive_candidate_assertion" not in fixes
    assert "merge_entities" not in fixes
    assert "deduplicate_assertions" not in fixes
