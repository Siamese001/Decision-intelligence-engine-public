"""Adversarial edge-case suite for Wave 1 Gate C1 Structural Census & Additive Extractor Conservation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import pytest

from dual_pipeline.document_pipeline_core.signal_merger import (
    AdditiveMergeViolationError,
    merge_specialized_mutations_additive,
    validate_structural_census_conservation,
)
from dual_pipeline.document_pipeline_core.tabular_row_extractor import (
    compute_raw_structural_census,
    extract_tabular_workbook_and_table_projection,
    reconcile_tabular_row_mutations,
)


def test_boundary_payload_empty_and_zero_row_documents() -> None:
    """Category 1 (Boundary payload): Empty strings and header-only tables do not crash or divide by zero."""
    census = compute_raw_structural_census("", {})
    assert census["page_count"] == 0
    assert census["table_row_count"] == 0
    assert census["substantive_unit_count"] == 0

    proj = extract_tabular_workbook_and_table_projection("", {})
    assert proj["graph_mutations"]["entity_upserts"] == []
    assert proj["graph_mutations"]["relationship_signals"] == []


def test_malformed_input_ragged_markdown_tables_and_non_dict_entries() -> None:
    """Category 2 (Malformed input): Ragged markdown pipes, nan/null cells, and malformed entity entries."""
    ragged_md = (
        "## Sheet: Broken Tab\n"
        "| ColA | ColB | ColC |\n"
        "|---|---|\n"
        "| only_one_cell |\n"
        "| nan | None | null |\n"
        "| ValidField | MappedValue | Active | ExtraCell |\n"
    )
    census = compute_raw_structural_census(ragged_md, {})
    assert census["table_row_count"] == 2
    proj = extract_tabular_workbook_and_table_projection(ragged_md, {"title": "Broken Workbook"})
    assert len(proj["graph_mutations"]["entity_upserts"]) == 3

    target = {
        "evidence_index": {"ev-1": {"text": "valid"}},
        "graph_mutations": {"entity_upserts": [None, "bad_entry", {"id": "system:ok", "name": "OK", "evidence_ids": ["ev-1"]}]},
    }
    receipt = merge_specialized_mutations_additive(target, {"evidence_index": {}, "graph_mutations": {"entity_upserts": [None]}})
    assert receipt["status"] == "PASS"
    assert len(target["graph_mutations"]["entity_upserts"]) == 1


def test_concurrency_parallel_census_and_additive_merges() -> None:
    """Category 3 (Concurrency & race): 16 concurrent threads running census and additive merges without shared state corruption."""
    doc = "## Sheet: S1\n| F | V |\n|---|---|\n| A | 10 |\n| B | 20 |\n"

    def _worker(idx: int) -> int:
        c = compute_raw_structural_census(doc, {"idx": idx})
        p = extract_tabular_workbook_and_table_projection(doc, {"title": f"Doc {idx}"})
        base = {"evidence_index": {}, "graph_mutations": {"entity_upserts": [], "relationship_signals": []}}
        merge_specialized_mutations_additive(base, p, census=c)
        return len(base["graph_mutations"]["entity_upserts"])

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_worker, range(32)))
    assert all(r == 3 for r in results)


def test_overflow_large_87_row_field_mapping_sheet() -> None:
    """Category 4 (Overflow & exhaustion): 87-row field map sheet extracts all 87 rows without budget truncation."""
    rows = ["## Sheet: Field Map (all 87)", "| Field | Rule |", "|---|---|"]
    for i in range(1, 88):
        rows.append(f"| sf_field_{i:02d} | HR360_Rule_{i:02d} |")
    doc = "\n".join(rows)
    census = compute_raw_structural_census(doc, {})
    assert census["table_row_count"] == 87

    proj = extract_tabular_workbook_and_table_projection(doc, {"document_title": "Position Field Map"})
    # 1 root entity + 87 row entities = 88
    assert len(proj["graph_mutations"]["entity_upserts"]) == 88
    assert len(proj["graph_mutations"]["relationship_signals"]) == 87
    assert proj["max_mutation_budget"] >= 175


def test_invariant_bypass_gate_c1_detects_signal_drop_and_unit_drop() -> None:
    """Category 5 (Invariant bypass attempt): Gate C1 raises AdditiveMergeViolationError on signal loss or empty evidence."""
    with pytest.raises(AdditiveMergeViolationError, match="ERR_ADDITIVE_MERGE_VIOLATION"):
        validate_structural_census_conservation(
            pre_counts={"grounded_entities": 5, "grounded_signals": 4},
            post_data={"evidence_index": {"ev-1": {}}, "graph_mutations": {"entity_upserts": [], "relationship_signals": []}},
        )

    with pytest.raises(AdditiveMergeViolationError, match="ERR_STRUCTURAL_UNIT_DROP"):
        validate_structural_census_conservation(
            pre_counts={"grounded_entities": 0, "grounded_signals": 0},
            post_data={"evidence_index": {}, "graph_mutations": {}},
            census={"substantive_unit_count": 5},
        )


def test_idempotent_re_merge_and_recovery() -> None:
    """Category 6 (Rollback & recovery): Re-merging the same specialized projection is idempotent with zero duplicate bloat."""
    base = {
        "evidence_index": {"ev-1": {"text": "Alpha"}},
        "graph_mutations": {
            "entity_upserts": [{"id": "system:alpha", "type": "system", "name": "Alpha", "description": "Short", "evidence_ids": ["ev-1"]}],
            "relationship_signals": [{"signal_id": "sig-1", "from_id": "system:alpha", "to_id": "system:beta", "relationship": "USES", "evidence_ids": ["ev-1"]}],
        },
    }
    spec = {
        "evidence_index": {"ev-2": {"text": "Alpha detailed spec"}},
        "graph_mutations": {
            "entity_upserts": [{"id": "system:alpha", "type": "system", "name": "Alpha", "description": "Detailed specification for Alpha", "evidence_ids": ["ev-2"]}],
            "relationship_signals": [{"signal_id": "sig-1", "from_id": "system:alpha", "to_id": "system:beta", "relationship": "USES", "evidence_ids": ["ev-2"]}],
        },
    }
    merge_specialized_mutations_additive(base, copy.deepcopy(spec), prefer_specialized=True)
    snap_after_first = copy.deepcopy(base)
    merge_specialized_mutations_additive(base, copy.deepcopy(spec), prefer_specialized=True)
    assert len(base["graph_mutations"]["entity_upserts"]) == 1
    assert len(base["graph_mutations"]["relationship_signals"]) == 1
    assert base["graph_mutations"]["entity_upserts"] == snap_after_first["graph_mutations"]["entity_upserts"]


def test_exact_production_regression_position_field_mapping_and_gold_dataset() -> None:
    """Category 7 (Exact production regression): Reconciles 2-entity Position Field Mapping stub into full row-level graph."""
    stub_data = {
        "evidence_index": {
            "ev-01": {"text": "Identify every SuccessFactors Position field", "locator": {"kind": "section", "value": "Read Me!R1"}},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:hr-360", "type": "system", "name": "HR 360", "evidence_ids": ["ev-01"]},
                {"id": "system:successfactors", "type": "system", "name": "SuccessFactors", "evidence_ids": ["ev-01"]},
            ],
            "relationship_signals": [
                {"signal_id": "rel-1", "from_id": "system:hr-360", "to_id": "system:successfactors", "relationship": "INTEGRATES_WITH", "evidence_ids": ["ev-01"]},
            ],
        },
    }
    raw_workbook_text = (
        "## Sheet: Read Me\n"
        "| Note | Detail |\n|---|---|\n"
        "| Goal | Identify every SuccessFactors Position field |\n\n"
        "## Sheet: Field Map (all 87)\n"
        "| Field | Source | Behavior |\n|---|---|---|\n"
        "| positionCode | HR 360 | Required on CREATE |\n"
        "| jobCode | HR 360 | Derived from Job Profile |\n"
        "| department | HR 360 | Synchronized on UPDATE |\n"
        "| location | SuccessFactors | Defaulted by OnSave rule |\n"
    )
    meta = {"document_title": "Position Integration Field Mapping"}
    changes = reconcile_tabular_row_mutations(stub_data, meta, raw_workbook_text)
    assert any("Merged" in c for c in changes)
    assert len(stub_data["graph_mutations"]["entity_upserts"]) >= 7
    assert len(stub_data["graph_mutations"]["relationship_signals"]) >= 6
