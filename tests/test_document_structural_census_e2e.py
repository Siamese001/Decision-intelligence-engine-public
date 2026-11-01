"""End-to-end verification of Wave 1 Gate C1 Structural Census and Additive Extractor Conservation."""

from __future__ import annotations

import json
from typing import Any, Dict

from dual_pipeline.document_pipeline_core.document_signal_router import (
    DocumentArchetype,
    apply_document_signal_budget_to_candidates,
    evaluate_document_signal_routing,
)
from dual_pipeline.document_pipeline_core.format_source_units import build_text_source_units
from dual_pipeline.document_pipeline_core.ingestion_planner import extract_role_framework_catalog_projection
from dual_pipeline.document_pipeline_core.signal_merger import (
    merge_specialized_mutations_additive,
)
from dual_pipeline.document_pipeline_core.tabular_row_extractor import (
    compute_raw_structural_census,
    extract_tabular_workbook_and_table_projection,
    reconcile_tabular_row_mutations,
)
from dual_pipeline.transcript_stages_core.graph_post_risk_healer import prune_and_budget_signals
from dual_pipeline.transcript_stages_core.graph_post_helpers import _normalize_entities


def test_multi_sheet_workbook_structural_census_and_row_extraction_e2e() -> None:
    """Verify multi-sheet field mapping workbook census and row-level signal extraction."""
    workbook_md = (
        "## Sheet: Read Me\n"
        "| Section | Purpose |\n"
        "|---|---|\n"
        "| Overview | Identify every SuccessFactors Position field for HR 360 integration. |\n\n"
        "## Sheet: Field Map (all 87)\n"
        "| SF Field | HR 360 Source | Action |\n"
        "|---|---|---|\n"
        "| positionCode | pos_id | CREATE |\n"
        "| jobTitle | title_txt | UPDATE |\n"
        "| costCenter | cc_code | CREATE |\n\n"
        "## Sheet: Business Rules\n"
        "| Rule ID | Trigger | Logic |\n"
        "|---|---|---|\n"
        "| BR-01 | OnSave | Populate department default from costCenter |\n"
        "| BR-02 | OnInit | Validate positionCode format |\n"
    )
    census = compute_raw_structural_census(workbook_md, {"document_title": "Position Integration Field Mapping"})
    assert census["sheet_names"] == ["Read Me", "Field Map (all 87)", "Business Rules"]
    assert census["table_row_count"] == 6
    assert census["non_empty_row_count_per_sheet"]["Field Map (all 87)"] == 3
    assert census["non_empty_row_count_per_sheet"]["Business Rules"] == 2

    units = build_text_source_units(workbook_md, "abc123456789", "md")
    table_units = [u for u in units if u.unit_type == "table_block"]
    assert len(table_units) == 3
    assert table_units[1].metadata["data_rows_count"] == 3

    proj = extract_tabular_workbook_and_table_projection(
        workbook_md, {"document_title": "Position Integration Field Mapping"}
    )
    upserts = proj["graph_mutations"]["entity_upserts"]
    signals = proj["graph_mutations"]["relationship_signals"]
    assert len(upserts) >= 7
    assert len(signals) >= 6
    descriptions = " ".join(e.get("description", "") for e in upserts)
    assert "positionCode" in descriptions
    assert "BR-01" in descriptions


def test_gold_dataset_ampersand_and_quantitative_value_preservation_e2e() -> None:
    """Verify Gold Dataset Asset Count preserves ampersands and quantitative counts/percentages."""
    gold_proj: Dict[str, Any] = {
        "evidence_index": {
            "ev-01": {"text": "Pitch & Rotor Total", "exact_text": "Pitch & Rotor Total", "locator": {"kind": "table_row", "value": "Pitch & Rotor Total"}},
            "ev-02": {"text": "123", "exact_text": "123", "locator": {"kind": "table_value", "value": "123"}},
            "ev-03": {"text": "38.2%", "exact_text": "38.2%", "locator": {"kind": "table_value", "value": "38.2%"}},
            "ev-04": {"text": "Electrical & Controls Total", "exact_text": "Electrical & Controls Total", "locator": {"kind": "table_row", "value": "Electrical & Controls Total"}},
            "ev-05": {"text": "80", "exact_text": "80", "locator": {"kind": "table_value", "value": "80"}},
            "ev-06": {"text": "24.8%", "exact_text": "24.8%", "locator": {"kind": "table_value", "value": "24.8%"}},
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "metric:pitch-and-rotor-asset-count",
                    "type": "metric",
                    "name": "Pitch & Rotor Total",
                    "description": "123 assets.",
                    "evidence_ids": ["ev-01", "ev-02", "ev-03"],
                },
                {
                    "id": "metric:electrical-and-controls-asset-count",
                    "type": "metric",
                    "name": "Electrical & Controls Total",
                    "description": "80 assets.",
                    "evidence_ids": ["ev-04", "ev-05", "ev-06"],
                },
            ],
            "relationship_signals": [],
        },
    }
    _normalize_entities(gold_proj["graph_mutations"]["entity_upserts"], [])
    ents = gold_proj["graph_mutations"]["entity_upserts"]
    assert ents[0]["name"] == "Pitch & Rotor Total"
    assert ents[1]["name"] == "Electrical & Controls Total"

    meta: Dict[str, Any] = {"document_title": "Gold Dataset Asset Count By Asset Category"}
    changes = reconcile_tabular_row_mutations(gold_proj, meta, "Pitch & Rotor Total 123 38.2% Electrical & Controls Total 80 24.8%")
    assert changes
    assert "38.2%" in ents[0]["description"]
    assert "24.8%" in ents[1]["description"]
    assert len(gold_proj["graph_mutations"]["relationship_signals"]) >= 1


def test_additive_specialized_merge_conserves_llm_signals_e2e() -> None:
    """Verify prefer_specialized=True merges specialized and grounded LLM signals additively without wiping."""
    final_data: Dict[str, Any] = {
        "evidence_index": {
            "ev-llm-1": {"text": "PartnerAdvisory leads AI Software Factory - Partner Prototype.", "exact_text": "PartnerAdvisory leads AI Software Factory - Partner Prototype."},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "description": "Delivery partner", "evidence_ids": ["ev-llm-1"]},
                {"id": "workstream:ai-software-factory", "type": "workstream", "name": "AI Software Factory - Partner Prototype", "description": "Prototype", "evidence_ids": ["ev-llm-1"]},
            ],
            "relationship_signals": [
                {"signal_id": "sig-llm-1", "from_id": "org:partner_advisory", "to_id": "workstream:ai-software-factory", "relationship": "DELIVERS", "evidence_ids": ["ev-llm-1"]}
            ],
            "decision_updates": [
                {"decision_id": "dec-llm-1", "description": "Approved Q4 prototype charter", "evidence_ids": ["ev-llm-1"]}
            ],
            "risk_updates": [
                {"risk_id": "risk-llm-1", "description": "API save trigger gap", "evidence_ids": ["ev-llm-1"]}
            ],
        },
    }
    special_proj: Dict[str, Any] = {
        "max_mutation_budget": 120,
        "evidence_index": {
            "ev-sp-1": {"text": "Contractual SOW deliverable milestone 1.", "exact_text": "Contractual SOW deliverable milestone 1."},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "workstream:sow-milestone-1", "type": "workstream", "name": "SOW Milestone 1", "description": "Milestone 1", "evidence_ids": ["ev-sp-1"]},
            ],
            "relationship_signals": [
                {"signal_id": "sig-sp-1", "from_id": "org:partner_advisory", "to_id": "workstream:sow-milestone-1", "relationship": "OWNS", "evidence_ids": ["ev-sp-1"]}
            ],
            "decision_updates": [],
            "risk_updates": [],
        },
    }
    receipt = merge_specialized_mutations_additive(final_data, special_proj, prefer_specialized=True)
    assert receipt["status"] == "PASS"
    ent_ids = {e["id"] for e in final_data["graph_mutations"]["entity_upserts"]}
    assert "org:partner_advisory" in ent_ids
    assert "workstream:ai-software-factory" in ent_ids
    assert "workstream:sow-milestone-1" in ent_ids
    assert len(final_data["graph_mutations"]["relationship_signals"]) == 2
    assert len(final_data["graph_mutations"]["decision_updates"]) == 1
    assert len(final_data["graph_mutations"]["risk_updates"]) == 1


def test_document_budget_and_router_preserve_grounded_candidates_e2e() -> None:
    """Verify grounded document signals are not sliced by _enforce_mutation_budget or router caps."""
    ev_index = {f"ev-{i}": {"text": f"Fact {i}"} for i in range(45)}
    mutations: Dict[str, Any] = {
        "entity_upserts": [
            {"id": f"system:sys-{i}", "type": "system", "name": f"Sys {i}", "evidence_ids": [f"ev-{i}"]}
            for i in range(45)
        ],
        "relationship_signals": [
            {"signal_id": f"sig-{i}", "from_id": "system:sys-0", "to_id": f"system:sys-{i}", "relationship": "INTEGRATES_WITH", "evidence_ids": [f"ev-{i}"]}
            for i in range(1, 45)
        ],
    }
    prune_and_budget_signals(mutations, ["ev-0"], is_document=True, ev_index=ev_index, metadata={"max_mutation_budget": 120})
    assert len(mutations["entity_upserts"]) == 45
    assert len(mutations["relationship_signals"]) == 44

    sow_decision = evaluate_document_signal_routing("## Scope of Work\nDeliverables.", title="Statement of Work")
    assert sow_decision.archetype == DocumentArchetype.SOW_CONTRACT
    cands = [
        {
            "candidate_id": f"cand-{i}",
            "candidate_type": "architecture_relationship",
            "evidence_ids": [f"ev-{i}"],
            "source_locator": {"kind": "section", "value": "## Scope of Work"},
        }
        for i in range(25)
    ]
    calibrated = apply_document_signal_budget_to_candidates(cands, sow_decision)
    assert all(c["document_signal_disposition"] == "APPROVED" for c in calibrated)
