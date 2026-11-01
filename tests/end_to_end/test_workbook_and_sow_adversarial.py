"""Wave 3 E2E & adversarial tests for Visual Org-Chart, SOW Parser, and Workbook Slicer."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from dual_pipeline.canonical_pipeline.visual_org_extractor import (
    extract_visual_org_chart_topology,
)
from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_sow_contract_projection,
)
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    extract_staffing_workbook_projection,
)


def test_adversarial_1800_row_workbook_exhaustion_and_placeholder_headers() -> None:
    """Category: Overflow & exhaustion + Boundary payload + Malformed/unfilled placeholders."""
    lines = ["Client: <Placeholder> | Project: TBD | ENDUR ETRM TRM Lakehouse"]
    for i in range(1, 19):
        rate = "$0" if i == 10 else "$225"
        lines.append(f"TBD{i} | Specialist {i} | Rate {rate} | Total Hours: 1670")
    for i in range(19, 101):
        lines.append(f"TBD{i} | Empty | Rate $0 | Total Hours: 0")
    for i in range(1800):
        lines.append(f"Germany Static Rate Lookup Row {i} | $195")

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmpdir:
        res = extract_staffing_workbook_projection("\n".join(lines))
        out_path = Path(tmpdir) / "03_workbook_signals.json"
        out_path.write_text(json.dumps(res, indent=2), encoding="utf-8")

        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert loaded["workbook_slice_audit"]["active_roles_count"] == 18
        assert loaded["workbook_slice_audit"]["quarantined_q3_rows_count"] == 1882
        node_ids = {n["id"] for n in loaded["graph_mutations"]["entity_upserts"]}
        assert not any(f"role:tbd{i}" in node_ids for i in range(19, 101))


def test_adversarial_sow_boilerplate_injection_and_boundary_payload() -> None:
    """Category: Invariant bypass attempt (legal boilerplate) + Boundary payload."""
    spoof_sow = """
    STATEMENT OF WORK: ENTERPRISE4U Align + PRF Integration
    Section 2.2 Activities: Workstream A (Align) and Workstream B (PRF Integration)
    Section 2.3 Out of Scope: Mainframe decommissioning is Out of Scope.
    Section 6 Rate Card: Gross $618,390, Rebate $165,310, Net $484,000.
    Section 12 Legal Boilerplate:
    Under the Sherman Act and Clayton Act, Securities and Exchange Commission and
    Indemnification and Hold Harmless clauses apply in perpetuity.
    """
    res = extract_sow_contract_projection(spoof_sow)
    assert res["sow_boilerplate_audit"]["quarantined_q5_clause_count"] >= 2
    serialized = json.dumps(res["graph_mutations"]["entity_upserts"])
    assert "Sherman Act" not in serialized
    assert "Clayton Act" not in serialized


def test_adversarial_visual_org_ui_chrome_spoofing_and_malformed_lines() -> None:
    """Category: Malformed input + Invariant bypass attempt (UI controls spoofing person nodes)."""
    malformed = "Search people... | Tree | Radial | Reset | Zoom In | Export PDF\n\x00\x01\nPetter Skantze -> Richard Argentieri"
    res = extract_visual_org_chart_topology(malformed)
    assert res["ui_chrome_stripped"] is True
    assert res["node_count"] == 2
    assert res["edge_count"] == 1
    labels = {n["label"] for n in res["nodes_added"]}
    for forbidden in ("Search people", "Tree", "Radial", "Reset", "Zoom In", "Export PDF"):
        assert forbidden not in labels


def test_adversarial_cross_sow_isolation_no_hardcoded_enterprise4u_leakage() -> None:
    """Category: Exact production regression + Invariant bypass attempt (cross-SOW hardcoded leakage)."""
    lens_sow = """
    # LENS - Visual Asset Intelligence System SOW
    ## SOW 1: Image Processing & Extraction
    Apex Global Analytics, LLC and PartnerAdvisory, LLC under Purchase Contract No. 4600027256.
    Technicians photograph nameplates via the Conduit mobile application and upload to AWS S3 for IBM Maximo write-back.
    SOW 2 covering Maximo write-back.
    ## Team Composition
    ## 1 Product Manager
    ## 1 ML / AI Engineer
    ## 1 Full Stack Engineer
    Average Team Member Blended Rate: $209/hour
    ## SOW Estimated Total: $421,344
    ## Out of Scope
    Maximo write-back in SOW 1 is outside the scope of this Statement of Work.
    """
    res = extract_sow_contract_projection(lens_sow)
    ent_ids = {e["id"] for e in res["graph_mutations"]["entity_upserts"]}
    assert "workstream:sow-1-image-processing-and-extraction" in ent_ids
    assert "workstream:sow-2-maximo-write-back" in ent_ids
    assert "metric:sow-estimated-total-421344" in ent_ids
    assert "metric:sow-blended-rate-209-hr" in ent_ids
    assert "workstream:workstream-a-align" not in ent_ids
    assert "workstream:workstream-b-prf-integration" not in ent_ids
    assert "metric:sow-gross-fees-618390" not in ent_ids
    assert "metric:sow-rebate-credit-165310" not in ent_ids
    assert "metric:sow-net-fees-484000" not in ent_ids

