"""Wave 1 adversarial and edge-case tests for SOW contract extractor."""
from __future__ import annotations

from collections import Counter

from dual_pipeline.document_pipeline_core.sow_contract_extractor import (
    extract_sow_contract_projection,
)


def test_adversarial_boundary_empty_and_minimal_sow_payload() -> None:
    """Category: Boundary payload (empty input and single-line minimal SOW)."""
    res_empty = extract_sow_contract_projection("")
    upserts = res_empty["graph_mutations"]["entity_upserts"]
    rels = res_empty["graph_mutations"]["relationship_signals"]
    assert len(upserts) >= 3
    deg = Counter()
    for r in rels:
        deg[r["from_id"]] += 1
        deg[r["to_id"]] += 1
    assert all(deg[e["id"]] >= 1 for e in upserts)


def test_adversarial_malformed_markdown_activity_and_fee_tables() -> None:
    """Category: Malformed input (truncated pipe rows, non-numeric rates, unclosed tags)."""
    malformed_sow = """
    # STATEMENT OF WORK ("SOW")
    ## ENTERPRISE4U – Employee Experience & Agent Foundational Framework – Align + PRF Integration
    ##### Workstream A — Align
    | # | Activity | PartnerAdvisory Responsibility | Client Responsibility | Estimated Timing |
    |---|----------|----------------------|
    | 1 | Discover – Build the Fact Base | Facilitate kickoff | Provide access | Week 1 |
    | X | Malformed Row Without Number | Broken |
    | 2 | Incomplete Pipe Row
    ### 6. FEES
    | Role | Rate ($) | Estimated Hours | Estimated Fees |
    | **Workstream A — Align** | | | |
    | Engagement Lead | $275 | 10 | $2,750 |
    | Corrupted Role Row | $NaN | TBD | $0 |
    | **Estimated Total Service Fees:** | | | **$618,390** |
    """
    res = extract_sow_contract_projection(malformed_sow)
    ent_ids = {e["id"] for e in res["graph_mutations"]["entity_upserts"]}
    assert "deliverable:activity-a1-discover-build-the-fact-base" in ent_ids
    assert "role:engagement-lead" in ent_ids
    assert "role:corrupted-role-row" not in ent_ids
    assert "role:practice-area-director" not in ent_ids


def test_adversarial_q5_legal_boilerplate_quarantine_and_production_regression() -> None:
    """Category: Invariant bypass attempt (Q5 legal boilerplate) + Exact production regression."""
    sow_with_boilerplate = """
    STATEMENT OF WORK: Apex Power & Grid Company ("Client" or "APEX") and PartnerAdvisory, Inc.
    Workstream A (Align) and Workstream B (PRF Integration) integrate HR 360 via TYK to HR master-data platform and recruiting platform.
    Parties shall comply with the Sherman Act, Clayton Act, Securities and Exchange Commission rules,
    and Indemnification and Hold Harmless and Survival of Representations clauses.
    """
    res = extract_sow_contract_projection(sow_with_boilerplate)
    assert res["sow_boilerplate_audit"]["quarantined_q5_clause_count"] >= 2
    upserts = res["graph_mutations"]["entity_upserts"]
    names = {e["name"] for e in upserts}
    assert "Apex Power & Grid Company" in names
    assert "PartnerAdvisory" in names
    assert not any("PartnerAdvisory-Florida" in n or "Sherman" in n or "Clayton" in n for n in names)
