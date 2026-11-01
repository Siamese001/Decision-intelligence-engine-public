"""Wave 3 E2E tests for Gate C3 Counterfactual Recall Probe, Judge defect fail-closed, and 8-page SUCR enforcement."""
from __future__ import annotations

import json
import pytest

from dual_pipeline.document_pipeline_core.quality import (
    qualify_document_judge,
    validate_slide_deck_coverage_and_yield,
)
from dual_pipeline.llm_judge_core.adversarial_probe import (
    CounterfactualRecallError,
    run_counterfactual_recall_probe,
    sample_stratified_source_probes,
)
from dual_pipeline.llm_judge_core.graph_checks import apply_deterministic_graph_scoring
from dual_pipeline.llm_judge_core.judges.graph import build_graph_judge_user_content


def _build_90_row_roster_source_and_projections():
    pages = []
    full_entities = []
    full_signals = []
    full_evidence = {}
    trunc_entities = []
    trunc_signals = []
    trunc_evidence = {}

    row_idx = 0
    for p in range(1, 9):
        rows = [
            f"## Page {p}",
            "| Area | Sub Capabilities | Lead | Specialist |",
            "| --- | --- | --- | --- |",
        ]
        count_on_page = 12 if p < 8 else 6
        for k in range(count_on_page):
            row_idx += 1
            ws_name = f"Q4 Charter Workstream {row_idx:02d}"
            lead_name = f"Lead Person {row_idx:02d}"
            spec_name = f"Contractor Specialist {row_idx:02d}"
            line = f"| Enterprise AI | {ws_name} | {lead_name} | {spec_name} |"
            rows.append(line)

            ev_id = f"ev-row-{row_idx:02d}"
            ev_obj = {
                "exact_text": f"Enterprise AI | {ws_name} | {lead_name} | {spec_name}",
                "locator": {"kind": "page", "value": f"Page {p}"},
            }
            ent_obj = {
                "id": f"workstream:q4-charter-{row_idx:02d}",
                "type": "workstream",
                "name": ws_name,
                "description": f"{ws_name} in Enterprise AI led by {lead_name} with specialist {spec_name}.",
                "evidence_ids": [ev_id],
            }
            sig_obj = {
                "signal_id": f"sig-row-{row_idx:02d}",
                "from_id": f"workstream:q4-charter-{row_idx:02d}",
                "predicate": "LED_BY",
                "to_id": f"person:lead-{row_idx:02d}",
                "claim": f"{ws_name} is led by {lead_name} and staffed by {spec_name}.",
                "evidence_ids": [ev_id],
            }
            full_evidence[ev_id] = ev_obj
            full_entities.append(ent_obj)
            full_signals.append(sig_obj)
            if row_idx <= 10:
                trunc_evidence[ev_id] = ev_obj
                trunc_entities.append(ent_obj)
                trunc_signals.append(sig_obj)
        pages.append("\n".join(rows))

    source_text = "\n\n".join(pages)
    full_proj = {
        "meeting_id": "document-20261020-nea-q4-planning-assignments",
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": full_evidence,
        "graph_mutations": {
            "entity_upserts": full_entities,
            "relationship_signals": full_signals,
        },
    }
    trunc_proj = {
        "meeting_id": "document-20261020-nea-q4-planning-assignments",
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": trunc_evidence,
        "graph_mutations": {
            "entity_upserts": trunc_entities,
            "relationship_signals": trunc_signals,
        },
    }
    return source_text, full_proj, trunc_proj


@pytest.mark.e2e
def test_gate_c3_counterfactual_recall_and_8_page_sucr_enforcement_e2e() -> None:
    """Verify Gate C3 stratified probe and 8-page SUCR gate reject the 10-project truncated PDF and pass the 90-row roster."""
    source_text, full_proj, trunc_proj = _build_90_row_roster_source_and_projections()

    probes = sample_stratified_source_probes(source_text, max_probes=10)
    assert len(probes) == 10

    # 1. Truncated 10-project projection fails Gate C3 recall (< 0.90) with ERR_OMITTED_WORKSTREAM_NODES
    trunc_probe_res = run_counterfactual_recall_probe(source_text, projection=trunc_proj, min_recall=0.90)
    assert trunc_probe_res["status"] == "FAIL"
    assert trunc_probe_res["recall"] < 0.90
    assert "ERR_OMITTED_WORKSTREAM_NODES" in trunc_probe_res["defect_codes"]

    with pytest.raises(CounterfactualRecallError):
        run_counterfactual_recall_probe(source_text, projection=trunc_proj, min_recall=0.90, enforce=True)

    # 2. Truncated 10-project projection on an 8-page PDF fails slide/page coverage (no page_count < 10 exemption)
    cov_ok_trunc, cov_errs_trunc, cov_tel_trunc = validate_slide_deck_coverage_and_yield(
        trunc_proj,
        source_text,
        metadata={"page_count": 8, "is_document": True},
        mutation_count=20,
    )
    assert cov_ok_trunc is False
    assert cov_tel_trunc["page_count"] == 8
    assert cov_tel_trunc["substantive_coverage_ratio"] < 0.90
    assert any("DOCUMENT_SLIDE_COVERAGE_GAP" in e for e in cov_errs_trunc)

    # 3. Complete 90-row projection passes both Gate C3 (recall == 1.0) and 8-page SUCR (ratio == 1.0)
    full_probe_res = run_counterfactual_recall_probe(source_text, projection=full_proj, min_recall=0.90, enforce=True)
    assert full_probe_res["status"] == "PASS"
    assert full_probe_res["recall"] == 1.0

    cov_ok_full, cov_errs_full, cov_tel_full = validate_slide_deck_coverage_and_yield(
        full_proj,
        source_text,
        metadata={"page_count": 8, "is_document": True},
        mutation_count=180,
    )
    assert cov_ok_full is True, f"Expected full 8-page roster to pass coverage, got {cov_errs_full}"
    assert cov_tel_full["substantive_coverage_ratio"] == 1.0


@pytest.mark.e2e
def test_judge_defect_codes_fail_closed_and_no_score_inflation_e2e() -> None:
    """Verify Judge scorecards with critical defect_codes fail closed and deterministic checks never inflate D5."""
    source_text, full_proj, trunc_proj = _build_90_row_roster_source_and_projections()

    user_prompt = build_graph_judge_user_content(
        "<meeting_archetype>DOCUMENT_MODE</meeting_archetype>",
        source_text,
        json.dumps(trunc_proj),
        metadata={"is_document": True},
        archetype="DOCUMENT_MODE",
    )
    assert "<deterministic_verification_receipt" not in user_prompt
    assert "<tabular_roster_governance>" not in user_prompt

    raw_scorecard = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 24,
        "dimension_3_semantic_atomicity": 15,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 7,
        "dimension_6_evidence_grounding_and_schema": 20,
        "composite_score": 96,
        "verdict": "PASS",
        "status": "PASS",
        "defect_codes": ["ERR_OMITTED_WORKSTREAM_NODES"],
        "forensic_rationale": "Omitted 79 of 89 workstream nodes from pages 2-8.",
    }
    checked = apply_deterministic_graph_scoring(
        dict(raw_scorecard),
        trunc_proj,
        source_text,
        archetype="DOCUMENT_MODE",
        pipeline_mode="DOCUMENT_MODE",
    )
    # D5 must remain 7 (not inflated to 10/15), and critical defect_codes must force verdict="FAIL"
    assert checked["dimension_5_exclusion_of_action_items"] == 7
    assert checked["verdict"] == "FAIL"
    assert checked["status"] == "FAIL"

    qualified, passed, _ = qualify_document_judge(dict(raw_scorecard))
    assert passed is False
    assert qualified["verdict"] == "FAIL"
    assert any("ERR_OMITTED_WORKSTREAM_NODES" in e for e in qualified.get("quality_failures", []))
