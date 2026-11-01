"""Adversarial & Edge-Case Test Suite for iPhone Microsoft Teams Org Chart Ingestion.

Covers 4 mandatory edge-case categories:
1. Boundary payload: Reporting to <Name> (0) leaf cards and truncated viewport screenshots.
2. Malformed input: OCR status bar noise, Admin suffix accounts, and missing commas.
3. Invariant bypass attempt: Rejecting HAS_ROLE downgrade of REPORTS_TO, 3-assertion stub truncation, and org-to-person REPORTS_TO edges.
4. Rollback & recovery: Idempotent re-ingestion across Matt Barrows vs Matthew Barrows and Clement-Hamel vs Clement Hamel without duplicate nodes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.canonical_pipeline.visual_org_extractor import (
    _canonicalize_person_name,
    extract_visual_org_projection,
)

ROOT = Path(__file__).resolve().parents[2]


def test_category1_boundary_payload_zero_reports_and_truncated_viewport() -> None:
    """Category 1 (Boundary payload): Reporting to <Name> (0) and truncated viewport (13 vs 9 visible)."""
    leaf_md = """# iPhone Teams Leaf Card
< Organization

Skantze, Petter
Executive Vice President

Argentieri, Rich
President, Apex Global Analytics (NEA)

Reporting to Argentieri, Rich (0)
"""
    proj_leaf = extract_visual_org_projection(leaf_md, project="nea")
    rels_leaf = proj_leaf["graph_mutations"]["relationship_signals"]
    reports_leaf = [r for r in rels_leaf if r["predicate"] == "REPORTS_TO"]
    assert len(reports_leaf) == 1
    assert reports_leaf[0]["from_id"] == "person:richard-argentieri"
    assert reports_leaf[0]["to_id"] == "person:petter-skantze"

    beebe_ocr = """
    11:44
    < Organization
    Aussprung, Chad
    Executive Director Business Management
    Beebe, James
    Director Product R&D
    Business Mgmt
    Reporting to Beebe, James (13)
    Rawat, Vikas
    Non-APEX-Profile Worker IT Data Scientist
    Madhavaram, Srishma
    Non-APEX-Profile Worker IT Developer
    Garcia, Enrique
    Non-APEX-Profile Worker IT Developer
    Lastowski, Michael
    Non-APEX-Profile Worker IT Business Analyst
    Estrada, Juan Adan
    Non-APEX-Profile Worker IT Developer
    Alperovich, Mikhail
    Non-APEX-DATA SCIENTIST-Level 1 - Associate (0 - 5 Ye...
    Akopian, Arsen
    Non-APEX-Profile Worker IT Engineer
    Arefi, Pouya
    Non-APEX-Profile Worker IT Engineer
    Bennetto, Jack
    Non-APEX-Profile Worker IT Engineer
    """
    proj_beebe = extract_visual_org_projection(beebe_ocr, project="bmo")
    rels_beebe = [r for r in proj_beebe["graph_mutations"]["relationship_signals"] if r["predicate"] == "REPORTS_TO"]
    direct_to_beebe = [r for r in rels_beebe if r["to_id"] == "person:james-beebe"]
    assert len(direct_to_beebe) == 9
    assert len(rels_beebe) == 10


def test_category2_malformed_input_ocr_noise_and_admin_suffix() -> None:
    """Category 2 (Malformed input): OCR status bar noise, Admin suffix accounts, and missing commas."""
    noisy_md = """10:09
5G 98%
< Organization

Alam, Tanvir
Project Director
Pricing&Analy

Reporting to Alam, Tanvir (2)

Marsh Admin, Andrew
Measurements & Standards Scientist
Pricing&Analy

Marsh, Andrew
Principal Product Manager
ECSC
"""
    proj = extract_visual_org_projection(noisy_md, project="edo")
    gm = proj["graph_mutations"]
    names = {e["name"] for e in gm["entity_upserts"]}
    assert "Andrew Marsh Admin" in names
    assert "Andrew Marsh" in names
    assert "5G 98%" not in names
    assert "< Organization" not in names

    reports = {(r["from_id"], r["to_id"]) for r in gm["relationship_signals"] if r["predicate"] == "REPORTS_TO"}
    assert ("person:andrew-marsh-admin", "person:tanvir-alam") in reports
    assert ("person:andrew-marsh", "person:tanvir-alam") in reports


def test_category3_invariant_bypass_attempt_no_stub_truncation_or_org_to_person() -> None:
    """Category 3 (Invariant bypass attempt): Reject HAS_ROLE downgrade, 3-stub truncation, and org-to-person REPORTS_TO."""
    hierarchy_md = """# Organizational Hierarchy
- **Petter Skantze** — Executive Vice President (reports to John Ketchum)
- **Richard Argentieri** — President, Apex Global Analytics (NEA) (reports to Petter Skantze)
- **Christopher Nunalee** — Executive Director, Energy Analytics (reports to Richard Argentieri)
- **Reid Schenk** — Senior Director, Energy Analytics (reports to Christopher Nunalee)
- **Diana Valencia** — Principal Data Scientist (reports to Reid Schenk)
"""
    proj = extract_visual_org_projection(hierarchy_md, project="nea")
    gm = proj["graph_mutations"]
    reports = [r for r in gm["relationship_signals"] if r["predicate"] == "REPORTS_TO"]
    assert len(reports) == 5, "Must not truncate hierarchy to a 3-assertion stub"

    entity_types = {e["id"]: e["type"] for e in gm["entity_upserts"]}
    for r in reports:
        assert entity_types.get(r["from_id"]) == "person", f"Org node must never report to person: {r}"
        assert entity_types.get(r["to_id"]) == "person", f"REPORTS_TO target must be person: {r}"


def test_category4_rollback_and_recovery_idempotent_name_canonicalization() -> None:
    """Category 4 (Rollback & recovery): Idempotent re-ingestion across split-identity name variants."""
    assert _canonicalize_person_name("Matt Barrows") == "Matthew Barrows"
    assert _canonicalize_person_name("Matthew Barrows") == "Matthew Barrows"
    assert _canonicalize_person_name("Clement-Hamel") == "Clement Hamel"
    assert _canonicalize_person_name("Clement Hamel") == "Clement Hamel"
    assert _canonicalize_person_name("Rich Argentieri") == "Richard Argentieri"
    assert _canonicalize_person_name("Chris Nunalee") == "Christopher Nunalee"

    doc_a = "- **Matt Barrows** — Director, IT (reports to John Ketchum)\n- **Clement-Hamel** — Principal Engineer (reports to Matt Barrows)\n"
    doc_b = "- **Matthew Barrows** — Director, IT (reports to John Ketchum)\n- **Clement Hamel** — Principal Engineer (reports to Matthew Barrows)\n"
    proj_a = extract_visual_org_projection(doc_a, project="nea")
    proj_b = extract_visual_org_projection(doc_b, project="nea")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT UNIQUE)")
        for p in (proj_a, proj_b):
            for ent in p["graph_mutations"]["entity_upserts"]:
                conn.execute(
                    "INSERT OR REPLACE INTO node VALUES (?, ?)",
                    (ent["id"], ent["name"]),
                )
        conn.commit()
        rows = conn.execute("SELECT node_id, canonical_name FROM node ORDER BY node_id").fetchall()
        node_ids = {r[0] for r in rows}
        assert "person:matthew-barrows" in node_ids
        assert "person:matt-barrows" not in node_ids
        assert "person:clement-hamel" in node_ids
        clement_names = [r[1] for r in rows if r[0] == "person:clement-hamel"]
        assert clement_names == ["Clement Hamel"]
    finally:
        conn.close()
