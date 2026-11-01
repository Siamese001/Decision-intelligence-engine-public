"""Wave 3 unit tests: Visual Org-Chart Tracer, SOW Contractual Parser, Staffing Workbook Slicer & Offering Playbook."""

from __future__ import annotations

from pathlib import Path

from dual_pipeline.canonical_pipeline.visual_org_extractor import (
    extract_visual_org_chart_topology,
    extract_visual_org_projection,
)
from dual_pipeline.document_pipeline import load_document_content
from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_offering_playbook_projection,
    extract_sow_contract_projection,
)
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    extract_staffing_workbook_projection,
)


def test_visual_org_chart_extracts_iphone_teams_nodes_edges_champions_and_spans() -> None:
    raw_text = """
    < Organization
    Skantze, Petter
    EVP & Chief Risk Officer
    Argentieri, Richard
    President (NEA), VP Product Development
    Barrows, Matthew
    VP Enterprise, AI Products
    Reporting to Barrows, Matthew (7)
    Chokshi, Anuj
    Senior Director, Enterprise AI Products
    Andersen, John
    Director, Business Services AI Products
    Khan, Farrukh
    Senior Director, Business Operations AI Products
    Paynter, Gordon
    Director, Product Management
    Bartlett, Lori
    Senior Director, T&D Asset Optimization
    Bryant, Jarod
    Executive Director, Generation Asset Optimization
    Kaufman, Ashley
    Administrative Technician
    """
    res = extract_visual_org_chart_topology(raw_text)
    assert res["node_count"] == 10
    assert res["edge_count"] == 9
    assert res["collapsed_span_counts"] == {"person:matthew-barrows": 7}
    assert res["ui_chrome_stripped"] is True
    edges = res["edges_added"]
    assert any(
        e["from"] == "person:richard-argentieri"
        and e["to"] == "person:petter-skantze"
        and e["type"] == "REPORTS_TO"
        for e in edges
    )
    assert any(
        e["from"] == "person:jarod-bryant"
        and e["to"] == "person:matthew-barrows"
        and e["type"] == "REPORTS_TO"
        for e in edges
    )
    node_ids = {n["id"] for n in res["nodes_added"]}
    assert not any("organization" in nid for nid in node_ids)

    proj = extract_visual_org_projection(raw_text)
    assert len(proj["graph_mutations"]["entity_upserts"]) == 10
    assert len(proj["graph_mutations"]["relationship_signals"]) == 9


def test_sow_contract_extractor_workstreams_rates_metrics_and_legal_quarantine() -> None:
    sow_text = """
    STATEMENT OF WORK: ENTERPRISE4U Align + PRF Integration
    2.1 Scope:
    Workstream A (Align): Modernize HR4U and HR 360 experience with Moveworks and TYK gateway.
    Workstream B (PRF Integration): Integrate Ataccama and Immuta data governance controls.
    2.3 Out of Scope:
    Legacy PeopleSoft payroll modifications and non-HR ERP modules are strictly Out of Scope.
    6.1 Fees and Rate Card:
    Gross Fees: $618,390
    Promotional Rebate Credit: $165,310
    Net Fixed Fee: $484,000
    Coordinators and Signatories: Drew Oakley, Clement Hamel, Beau Williamson
    11.4 Antitrust & Legal Compliance:
    Parties shall comply with the Clayton Act and Sherman Act and Indemnification and Hold Harmless provisions.
    """
    res = extract_sow_contract_projection(sow_text)
    upserts = res["graph_mutations"]["entity_upserts"]
    ent_by_id = {e["id"]: e for e in upserts}

    assert "workstream:workstream-a-align" in ent_by_id
    assert "workstream:workstream-b-prf-integration" in ent_by_id

    role_entities = [e for e in upserts if e["type"] == "role"]
    assert len(role_entities) == 9

    assert "metric:sow-gross-fees-618390" in ent_by_id
    assert "metric:sow-rebate-credit-165310" in ent_by_id
    assert "metric:sow-net-fees-484000" in ent_by_id

    sys_names = {e["name"] for e in upserts if e["type"] == "system"}
    assert sys_names >= {"HR4U", "HR 360", "TYK", "Moveworks", "Ataccama", "Immuta"}

    person_names = {e["name"] for e in upserts if e["type"] == "person"}
    assert person_names == {"Drew Oakley", "Clement Hamel", "Beau Williamson"}

    assert len(res["graph_mutations"]["decision_updates"]) >= 1
    assert res["sow_boilerplate_audit"]["quarantined_q5_clause_count"] >= 1
    assert not any("Clayton" in e["name"] or "Sherman" in e["name"] for e in upserts)


def test_real_enterprise4u_sow_vs_lens_sow_zero_cross_contamination_and_ams_playbook() -> None:
    repo_root = next(
        (p for p in Path(__file__).resolve().parents if (p / "inputs" / "documents").exists()),
        Path(__file__).resolve().parents[1],
    )
    enterprise4u_path = repo_root / "documents/hrit/ENTERPRISE4U_Employee_Experience_Align_Plus_PRF_Integration_SOW.docx"
    lens_path = repo_root / "inputs/documents/LENS ΓÇô Visual Asset Intelligence SOW 6.12.26.docx"
    ams_path = repo_root / "inputs/documents/AMS_Training_and_Solution_Materials_AMS_101_Florida.pdf"

    if enterprise4u_path.exists() and lens_path.exists():
        enterprise4u_raw = load_document_content(enterprise4u_path)
        enterprise4u_txt = enterprise4u_raw[0] if isinstance(enterprise4u_raw, tuple) else str(enterprise4u_raw)
        lens_raw = load_document_content(lens_path)
        lens_txt = lens_raw[0] if isinstance(lens_raw, tuple) else str(lens_raw)

        enterprise4u_proj = extract_sow_contract_projection(enterprise4u_txt)
        lens_proj = extract_sow_contract_projection(lens_txt)

        enterprise4u_ids = {e["id"] for e in enterprise4u_proj["graph_mutations"]["entity_upserts"]}
        lens_ids = {e["id"] for e in lens_proj["graph_mutations"]["entity_upserts"]}

        # ENTERPRISE4U SOW must extract Align, PRF Integration, $618,390 gross, $165,310 rebate, $484,000 net, and Table 4 roles
        assert "workstream:workstream-a-align" in enterprise4u_ids
        assert "workstream:workstream-b-prf-integration" in enterprise4u_ids
        assert "metric:sow-gross-fees-618390" in enterprise4u_ids
        assert "metric:sow-rebate-credit-165310" in enterprise4u_ids
        assert "metric:sow-net-fees-484000" in enterprise4u_ids
        assert "person:dan-okray" in enterprise4u_ids
        assert "person:clement-hamel" in enterprise4u_ids
        assert "person:beau-williamson" in enterprise4u_ids
        for ev in enterprise4u_proj["evidence_index"].values():
            assert ev["exact_text"] in enterprise4u_txt

        # LENS SOW must extract LENS SOW 1 & SOW 2, $421,344 NTE, $209/hr blended rate, Conduit/AWS S3/Maximo, and 0 ENTERPRISE4U leakage
        assert "workstream:sow-1-image-processing-and-extraction" in lens_ids
        assert "workstream:sow-2-maximo-write-back" in lens_ids
        assert "metric:sow-estimated-total-421344" in lens_ids
        assert "metric:sow-blended-rate-209-hr" in lens_ids
        assert "system:lens" in lens_ids
        assert "system:conduit" in lens_ids
        assert "system:ibm-maximo" in lens_ids
        assert "workstream:workstream-a-align" not in lens_ids
        assert "workstream:workstream-b-prf-integration" not in lens_ids
        assert "metric:sow-gross-fees-618390" not in lens_ids
        assert "metric:sow-net-fees-484000" not in lens_ids
        assert "person:dan-okray" not in lens_ids
        for ev in lens_proj["evidence_index"].values():
            assert ev["exact_text"] in lens_txt

    if ams_path.exists():
        ams_raw = load_document_content(ams_path)
        ams_txt = ams_raw[0] if isinstance(ams_raw, tuple) else str(ams_raw)
        ams_proj = extract_offering_playbook_projection(ams_txt)
        ams_ids = {e["id"] for e in ams_proj["graph_mutations"]["entity_upserts"]}
        assert "workstream:agentic-managed-services" in ams_ids
        assert "system:partner_advisory-traverse" in ams_ids
        assert "person:anthony-marquardt" in ams_ids
        assert "person:lucy-bickford" in ams_ids
        assert "metric:ams-deal-rbi-9-6m-follow-on" in ams_ids
        assert "metric:ams-deal-state-child-welfare-14-4m" in ams_ids
        for ev in ams_proj["evidence_index"].values():
            assert ev["exact_text"] in ams_txt


def test_staffing_workbook_slicer_active_roles_waves_and_risk_alerts() -> None:
    lines = [
        "Client Name: [Insert Client Name] | Project Name: [Insert Project Name]",
        "ENDUR ETRM FO Reporting MO Reporting BO Reporting TRM Lakehouse",
        "Discovery/Mobilize: 1,200 hrs | Wave 1: 6,500 hrs | Wave 2: 8,200 hrs | Wave 3: 9,100 hrs | PROD DEPLOY: 5,074 hrs",
        "Total Hours: 30,074 | Fixed Fee: $8,640,637 | ATTENTION! YOUR DEAL SIZE REQUIRES A CONTINUOUS QUALITY ADVISOR.",
    ]
    for i in range(1, 19):
        rate = "$0.00" if i == 10 else "$235.00"
        lines.append(f"TBD{i} | Active Role {i} | Bill Rate: {rate} | Total Hours: 1670")
    for i in range(19, 101):
        lines.append(f"TBD{i} | Empty Placeholder | Bill Rate: $0.00 | Total Hours: 0")
    for i in range(1400):
        lines.append(f"United Kingdom Static Rate Lookup Row {i} | $180")

    res = extract_staffing_workbook_projection("\n".join(lines))
    audit = res["workbook_slice_audit"]
    assert audit["active_roles_count"] == 18
    assert audit["active_waves_count"] == 5
    assert audit["quarantined_q3_rows_count"] >= 1482

    upserts = res["graph_mutations"]["entity_upserts"]
    ent_ids = {e["id"] for e in upserts}
    assert "workstream:trm-lakehouse-implementation-on-databricks" in ent_ids
    assert "role:tbd1" in ent_ids
    assert "role:tbd18" in ent_ids
    assert "role:tbd19" not in ent_ids
    assert "metric:workbook-total-hours-30074" in ent_ids
    assert "metric:workbook-fixed-fee-8640637" in ent_ids

    risk_ids = {r["risk_id"] for r in res["graph_mutations"]["risk_updates"]}
    assert "risk:continuous-quality-advisor-required" in risk_ids
    assert "risk:unpriced-active-staffing-role-tbd10" in risk_ids

