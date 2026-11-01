"""
Unit tests for slide tabular parser and milestone anti-conflation validation.
"""
from __future__ import annotations

from dual_pipeline.attachment_core.tabular_parser import (
    extract_tabular_and_semantic_structures,
    parse_vision_claims,
    parse_vision_milestones,
    parse_vision_tables,
)
from dual_pipeline.normalization_core.milestones import (
    load_project_milestones,
    validate_milestone_metric,
)

SAMPLE_OCR_LINES = [
    "Deliverable Metrics Performance",
    "Assets from La Casa + PROD site data collection",
    "Auto-Approved Rate: % of assets that don't require human review (\"Accepted\")",
    "Extraction Accuracy; % of \"Accepted\" assets that correctly extracted data values",
    "OCR Accuracy: % of \"Accepted\" assets that correctly read a nameplate",
    "Target:",
    "• Milestone 1: ≥90% extraction accuracy (high-confidence predictions are accurate ≥85% of the time)",
    "• Milestone 2: ≥90% OCR accuracy on known nameplate type with an active rule",
    "Iteration",
    "Baseline",
    "Total",
    "Assets",
    "83",
    "Auto-Approved",
    "Rate",
    "83(100%)",
    "Extraction",
    "Accuracy % (M1)",
    "64(77,5%)",
    "OCR Accuracy",
    "% (M2)",
    "71(85.5%)",
    "1",
    "83",
    "83 (100%)",
    "73 (88.0%)",
    "75(90.4%)",
    "2",
    "315",
    "315 (100%)",
    "277 (88.0%)",
    "284(90.2%)",
    "3",
    "322",
    "307 (95.3%)",
    "275/307 (89.6%)",
    "289/314 (92%)",
    "Notes",
    "• With a golden dataset across ~50 asset types",
    "• Gold dataset's labels have been verified by human",
    "• M2 fixes: Removed punctuation and whitespace stripping",
    "• Expanded golden dataset",
    "• Decodes barcodes as potential extractable value",
]


def test_parse_vision_milestones():
    milestones = parse_vision_milestones(SAMPLE_OCR_LINES)
    assert len(milestones) == 2
    assert milestones[0]["id"] == "M1"
    assert "extraction accuracy" in milestones[0]["description"].lower()
    assert milestones[1]["id"] == "M2"
    assert "ocr accuracy" in milestones[1]["description"].lower()


def test_parse_vision_tables():
    tables = parse_vision_tables(SAMPLE_OCR_LINES)
    assert len(tables) == 1
    t = tables[0]
    assert t["table_name"] == "Deliverable Metrics Performance"
    assert "Iteration" in t["headers"][0]
    assert "Extraction Accuracy (M1)" in t["headers"][3]
    assert "OCR Accuracy (M2)" in t["headers"][4]
    assert len(t["rows"]) == 4
    # Baseline
    assert t["rows"][0][0] == "Baseline"
    assert t["rows"][0][1] == "83"
    # Iteration 3
    assert t["rows"][3][0] == "3"
    assert t["rows"][3][1] == "322"
    assert "89.6%" in t["rows"][3][3]
    assert "92%" in t["rows"][3][4]


def test_parse_vision_claims():
    claims = parse_vision_claims(SAMPLE_OCR_LINES)
    assert len(claims) >= 5
    claims_text = " ".join(c["claim"] for c in claims)
    assert "golden dataset" in claims_text
    assert "barcodes" in claims_text


def test_extract_tabular_and_semantic_structures():
    tables, milestones, claims = extract_tabular_and_semantic_structures(SAMPLE_OCR_LINES)
    assert len(tables) == 1
    assert len(milestones) == 2
    assert len(claims) >= 5


def test_load_project_milestones_ssot():
    data = load_project_milestones("project_lens")
    assert "milestones" in data
    m_dict = data["milestones"]
    assert "M1" in m_dict
    assert m_dict["M1"]["metric_name"] == "Extraction Accuracy"
    assert "M2" in m_dict
    assert m_dict["M2"]["metric_name"] == "OCR Accuracy"


def test_validate_milestone_metric():
    # Affirmed cases
    ok, err = validate_milestone_metric("M1", "Extraction Accuracy", "project_lens")
    assert ok is True
    assert err is None

    ok, err = validate_milestone_metric("M2", "OCR Accuracy", "project_lens")
    assert ok is True
    assert err is None

    # Inverted cases
    ok, err = validate_milestone_metric("M1", "OCR Accuracy", "project_lens")
    assert ok is False
    assert "MILESTONE_INVERSION_DETECTED" in err
    assert "DO NOT label OCR accuracy as M1" in err

    ok, err = validate_milestone_metric("M2", "Extraction Accuracy", "project_lens")
    assert ok is False
    assert "MILESTONE_INVERSION_DETECTED" in err


def test_extract_image_visual_with_slide():
    import sys
    from pathlib import Path
    from dual_pipeline.attachment_core.image_extractor import extract_image_visual

    if sys.platform != "darwin":
        return

    repo_root = Path(__file__).resolve().parent.parent
    candidate_roots = [repo_root, repo_root.parent.parent]
    slide_path = None
    for r in candidate_roots:
        cand = (
            r / ".worktrees" / "live_sprint_4_00_pm_et_client_partner_advisory"
            / "output" / "project_lens" / "10-08-2026" / "Sprint_4_00_Pm_Et_Ed_PartnerAdvisory"
            / "referenced_artifacts" / "deliverable_metrics_performance_slide.png"
        )
        if cand.exists():
            slide_path = cand
            break
    if not slide_path or not slide_path.exists():
        return

    res = extract_image_visual(slide_path)
    assert res.get("tables") is not None
    assert len(res["tables"]) == 1
    assert res["tables"][0]["table_name"] == "Deliverable Metrics Performance"
    assert len(res["tables"][0]["rows"]) == 4
    assert res.get("milestones") is not None
    assert len(res["milestones"]) == 2
    assert res["milestones"][0]["id"] == "M1"
    assert res["milestones"][1]["id"] == "M2"

