"""Hermetic End-to-End Test Suite for iPhone Microsoft Teams Org Chart Ingestion & Graph Mutation."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from dual_pipeline.canonical_pipeline import visual_org_extractor
from dual_pipeline.canonical_pipeline.visual_org_extractor import extract_visual_org_projection
from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.executor import _resolve_document_raw_graph

ROOT = Path(__file__).resolve().parents[2]


def test_e2e_non_teams_specimen_purged_from_extractor_and_graph() -> None:
    """Verify non-Teams Oct 22 24-node web tree specimen is completely removed."""
    assert not hasattr(visual_org_extractor, "_NEA_24_NODE_TOPOLOGY")
    assert not hasattr(visual_org_extractor, "_parse_nea_visual_topology_tree")

    active_pair_path = ROOT / ".local/dual_pipeline/active_pair.json"
    if active_pair_path.exists():
        ap = json.loads(active_pair_path.read_text(encoding="utf-8"))
        db_path = ROOT / ".local/dual_pipeline" / ap["graph_path"]
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                alex_cnt = conn.execute(
                    "SELECT COUNT(*) FROM node WHERE lower(canonical_name) = 'alex kirsch'"
                ).fetchone()[0]
                assert alex_cnt == 0

                org_to_person_cnt = conn.execute(
                    """SELECT COUNT(*) FROM assertion a
                       JOIN node s ON s.node_id = a.subject_node_id
                       JOIN node o ON o.node_id = a.object_node_id
                       WHERE a.predicate = 'REPORTS_TO'
                         AND s.node_type = 'org'
                         AND o.node_type = 'stakeholder'"""
                ).fetchone()[0]
                assert org_to_person_cnt == 0
            finally:
                conn.close()


def test_e2e_iphone_teams_document_routing_to_sandboxed_sqlite() -> None:
    """Verify iPhone Teams org chart routes through archetype classifier, executor, and sandboxed SQLite."""
    sample_teams_md = """# James Beebe Business Management Org Chart
< Organization

Ketchum, John
Chairman, President & CEO

Skantze, Petter
Executive Vice President

Aussprung, Chad
VP, IT & Process Optimization

Beebe, James
Senior Director, Business Management
Business Mgmt

Reporting to Beebe, James (3)

Davenport, Jack
Principal Business Mgmt Spec

Farrington, John
Sr Business Mgmt Specialist

Perez, Lisette
Project Manager
"""
    archetype = classify_document_archetype(
        sample_teams_md, {"filename": "2026-10-12_James_Beebe_Org_Chart.md"}
    )
    assert archetype == DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART

    raw_graph_str = _resolve_document_raw_graph(
        None,
        sample_teams_md,
        {"document_archetype": archetype.value, "project": "bmo"},
        None,
    )
    proj = json.loads(raw_graph_str)
    gm = proj.get("graph_mutations", {})
    rels = gm.get("relationship_signals", [])
    reports_to = [r for r in rels if r.get("predicate") == "REPORTS_TO"]
    aff = [r for r in rels if r.get("predicate") == "AFFILIATED_WITH"]
    assert len(reports_to) == 6
    assert len(aff) == 1
    assert any(
        r["from_id"] == "person:james-beebe" and r["to_id"] == "person:chad-aussprung"
        for r in reports_to
    )

    sandbox_dir = Path(tempfile.mkdtemp(prefix="eval_sandbox_"))
    db_file = sandbox_dir / "sandbox_graph.sqlite"
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            "CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, title TEXT)"
        )
        conn.execute(
            "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT)"
        )
        for ent in gm.get("entity_upserts", []):
            conn.execute(
                "INSERT OR REPLACE INTO node VALUES (?, ?, ?, ?)",
                (ent["id"], ent["type"], ent["name"], ent.get("description", "")),
            )
        for sig in rels:
            if sig.get("predicate") in ("REPORTS_TO", "AFFILIATED_WITH"):
                conn.execute(
                    "INSERT OR REPLACE INTO assertion VALUES (?, ?, ?, ?)",
                    (sig["signal_id"], sig["from_id"], sig["predicate"], sig["to_id"]),
                )
        conn.commit()

        rep_cnt = conn.execute(
            "SELECT COUNT(*) FROM assertion WHERE predicate = 'REPORTS_TO'"
        ).fetchone()[0]
        aff_cnt = conn.execute(
            "SELECT COUNT(*) FROM assertion WHERE predicate = 'AFFILIATED_WITH'"
        ).fetchone()[0]
        assert rep_cnt == 6
        assert aff_cnt == 1
    finally:
        conn.close()


def test_e2e_production_graph_readback_across_all_iphone_teams_sources() -> None:
    """Verify active graph.sqlite readback for all remediated iPhone Teams org chart sources."""
    active_pair_path = ROOT / ".local/dual_pipeline/active_pair.json"
    assert active_pair_path.exists()
    ap = json.loads(active_pair_path.read_text(encoding="utf-8"))
    db_path = ROOT / ".local/dual_pipeline" / ap["graph_path"]
    assert db_path.exists()

    expected_min_reports_to = {
        "source-666922d694ebf04ee51bb32f": 113,
        "source-f0dd4f79f9a0d008f1f180bf": 16,
        "source-28c37119c87788bcbae2beb3": 12,
        "source-9b1aa0bb6670ea84747b4449": 9,
        "source-nea-20261012-energy-analytics-org": 6,
        "source-af30bee61106a581097d9777": 9,
        "source-ac4d0c3c8bb21f95267182a3": 10,
        "source-df6e9032b7c701a6912d5a53": 6,
        "source-030c17bd9bc498a5534ce15c": 9,
    }
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for sid, expected_cnt in expected_min_reports_to.items():
            actual_cnt = conn.execute(
                """SELECT COUNT(*) FROM assertion
                   WHERE primary_source_id = ?
                     AND predicate = 'REPORTS_TO'
                     AND canonical_status = 'canonical'
                     AND currentness_state = 'current'""",
                (sid,),
            ).fetchone()[0]
            assert actual_cnt >= expected_cnt, f"{sid}: expected >= {expected_cnt}, got {actual_cnt}"
    finally:
        conn.close()
