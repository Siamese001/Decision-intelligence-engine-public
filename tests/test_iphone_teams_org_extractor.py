"""Wave 1 unit verification: iPhone Microsoft Teams Org Chart Extractor & Archetype Routing."""
from __future__ import annotations

import inspect
from pathlib import Path

from dual_pipeline.canonical_pipeline import visual_org_extractor
from dual_pipeline.canonical_pipeline.visual_org_extractor import (
    extract_visual_org_projection,
)
from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_non_teams_web_tree_specimen_completely_removed() -> None:
    src = inspect.getsource(visual_org_extractor)
    assert "_NEA_24_NODE_TOPOLOGY" not in src
    assert "Kirk Cross" not in src
    assert "_parse_nea_visual_topology_tree" not in src


def test_batch1_oct14_complete_nee_leadership_hierarchy_20_screenshots() -> None:
    md_path = REPO_ROOT / "intake" / "2026-10-14_NEE_Organizational_Hierarchy_Leadership_Complete.md"
    text = md_path.read_text(encoding="utf-8")
    assert classify_document_archetype(text) == DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART

    proj = extract_visual_org_projection(md_path, project="enterprise_architecture")
    assert proj["meeting_archetype"] == "VISUAL_TOPOLOGY_AND_ORG_CHART"
    gm = proj["graph_mutations"]
    ents = gm["entity_upserts"]
    rels = gm["relationship_signals"]
    rep_rels = [r for r in rels if r.get("relationship") == "REPORTS_TO"]
    assert len(ents) == 114
    assert len(rep_rels) == 113
    assert all(r.get("relationship") == "REPORTS_TO" for r in rels)
    assert all(r.get("evidence_ids") for r in rep_rels)


def test_batch2_oct12_six_org_chart_markdown_packages() -> None:
    expected = {
        "2026-10-12_APEX_Development_Technology_Org_Chart.md": 16,
        "2026-10-12_NEA_Energy_Analytics_Org_Chart.md": 6,
        "2026-10-12_NEE_Executive_AI_Transformation_Org_Chart.md": 4,
        "2026-10-12_NEE_IT_Compute_Operations_Org_Chart.md": 10,
        "2026-10-12_NEE_Power_Gen_Operational_Excellence_Org_Chart.md": 16,
        "2026-10-12_NEE_SAP_IT_Business_Solutions_Org_Chart.md": 9,
    }
    total_rep = 0
    for fname, exp_count in expected.items():
        md_path = REPO_ROOT / "intake" / fname
        text = md_path.read_text(encoding="utf-8")
        assert classify_document_archetype(text) == DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
        proj = extract_visual_org_projection(md_path, project="enterprise_architecture")
        rep_rels = [
            r for r in proj["graph_mutations"]["relationship_signals"]
            if r.get("relationship") == "REPORTS_TO"
        ]
        assert len(rep_rels) == exp_count, f"{fname}: expected {exp_count}, got {len(rep_rels)}"
        total_rep += len(rep_rels)
    assert total_rep == 61


def test_batch3_oct12_james_beebe_bmo_iphone_teams_cards() -> None:
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
    assert classify_document_archetype(beebe_ocr) == DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
    proj = extract_visual_org_projection(beebe_ocr, project="bmo")
    gm = proj["graph_mutations"]
    rep_rels = [r for r in gm["relationship_signals"] if r.get("relationship") == "REPORTS_TO"]
    aff_rels = [r for r in gm["relationship_signals"] if r.get("relationship") == "AFFILIATED_WITH"]
    assert len(rep_rels) == 10
    assert any(
        r["from_id"] == "person:james-beebe" and r["to_id"] == "person:chad-aussprung"
        for r in rep_rels
    )
    assert len(aff_rels) == 1
    assert aff_rels[0]["to_id"] == "org:business-mgmt"


def test_batch4_and_batch5_nea_enterprise_ai_and_edo_tanvir_alam() -> None:
    nea_md = REPO_ROOT / "intake" / "2026-10-14_NEA_Enterprise_AI_Leadership_Org_Chart.md"
    proj_nea = extract_visual_org_projection(nea_md, project="nea")
    rep_nea = [
        r for r in proj_nea["graph_mutations"]["relationship_signals"]
        if r.get("relationship") == "REPORTS_TO"
    ]
    assert len(rep_nea) == 6

    edo_ocr = """
    11:43
    < Organization
    Ketchum, John
    Chairman and CEO Apex Global Enterprises
    Rubio, Alex
    Exec VP Engineering Construction & ISC
    Devlin, Daniel
    Executive Director Strategic Transforma
    Alam, Tanvir
    Project Director - E&C
    Pricing&Analy
    Reporting to Alam, Tanvir (6)
    Khoury, Cedric
    Principal Product Manager - ECSC
    Pinnisetty, Ravikumar
    IT Technology Leader
    Marsh Admin, Andrew
    Measurements & Standards Scientist
    Moloney, Francesca
    Sr Project Manager - E&C
    Marsh, Andrew
    Principal Product Manager - ECSC
    Roy, Partho sarothi
    Principal Product Manager - ECSC
    """
    proj_edo = extract_visual_org_projection(edo_ocr, project="edo")
    rep_edo = [
        r for r in proj_edo["graph_mutations"]["relationship_signals"]
        if r.get("relationship") == "REPORTS_TO"
    ]
    assert len(rep_edo) == 9
