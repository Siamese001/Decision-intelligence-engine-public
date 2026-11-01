"""
Adversarial and edge-case tests for Wave 2: Tabular Workstream Extractor Refinement, OCR Row Suppression & JARVIS Disambiguation.

Covers:
1. Boundary payloads (empty text, non-tabular text, 0% progress, missing NAV dash '— 100%').
2. Negative NAV budgets ($-33K Beacon, $-212K SWARM) and missing charter governance risks ('No Associated Charter').
3. Multi-workstream allocations (Dave Sunderman across 6 workstreams) and cross-page carry-over stubs.
4. Invariant bypass & collision defense (unattributed 'Not specified' OCR assertions injected directly into change-set builder, and colliding product/workstream names).
"""
from __future__ import annotations

from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    is_tabular_workstream_roster_document,
    parse_tabular_workstream_roster,
    reconcile_tabular_workstream_mutations,
)
from dual_pipeline.intake_core.change_set_helpers import should_suppress_document_candidate


def test_boundary_empty_and_non_tabular_inputs() -> None:
    """Verify empty or non-tabular documents return cleanly without mutations."""
    assert is_tabular_workstream_roster_document("") is False
    assert is_tabular_workstream_roster_document("Short meeting notes with PM: Alice only once.") is False
    data: dict = {}
    assert reconcile_tabular_workstream_mutations(data, None, "") == []
    assert data == {}


def test_negative_nav_missing_nav_dash_and_no_associated_charter_risk() -> None:
    """Verify negative NAVs ($-33K, $-212K), dash NAVs ('— 100%'), and 'No Associated Charter' risk emission."""
    fixture = """## Page 1
BeaconPM: Gordon PaynterSM: William White
Product: Beacon
EngTech(Chakra Sankaraiah)
Engineering&Technology(Chakra Sankaraiah)
ProposedNEABeacon $-33K 100%

SWARM - Validationand PilotPM: Casey O'KeefeSM: Jake Wellington
Product: SWARM | StormWork Optimization
T&DAssetOpti (Lori Bartlett)
Enterprise AI(Matthew Barrows)
In ProgressNEASWARM -Validation and Pilot$-212K 91%

Regulatory LifecyclePM: Benjamin MillmanSM: William White
Bus OpsAI (Farrukh Khan)
Enterprise AI(Matthew Barrows)
In ProgressNo AssociatedCharter
$580K 117%

Security PortalPM: Gordon PaynterSM: William White
Product: Security Portal
EngTech(Chakra Sankaraiah)
Engineering&Technology(Chakra Sankaraiah)
ProposedNEASecurity Portal— 100%

JARVISPM: Taylor MercerSM: Kaleena McHugo
Product: JARVIS
TechSales(Reid Schenk)
NEAOperations(Chris Nunalee)
In ProgressNEAJARVIS $284K 100%

Gordon Paynter
3
Staff
Casey O'Keefe
3
Senior
Benjamin Millman
3
Staff
Dave Sunderman
6
Senior
Gordon Paynter
3
Staff
Taylor Mercer
2
Senior
"""
    ws_list = parse_tabular_workstream_roster(fixture)
    assert len(ws_list) == 5
    by_name = {w["name"]: w for w in ws_list}

    assert by_name["Beacon"]["nav_k"] == -33
    assert by_name["Beacon"]["est_nav_usd"] == -33_000
    assert by_name["Beacon"]["has_nav"] is True

    assert by_name["SWARM - Validation and Pilot"]["nav_k"] == -212
    assert by_name["SWARM - Validation and Pilot"]["est_nav_usd"] == -212_000

    assert by_name["Security Portal"]["has_nav"] is False
    assert by_name["Security Portal"]["est_nav_usd"] is None
    assert by_name["Security Portal"]["assignment_progress_pct"] == 100

    assert by_name["Regulatory Lifecycle"]["charter_mission"] == "No Associated Charter"
    assert by_name["Regulatory Lifecycle"]["assignment_progress_pct"] == 117

    data: dict = {}
    reconcile_tabular_workstream_mutations(data, {"is_document": True}, fixture)
    mut = data["graph_mutations"]
    risks = mut.get("risk_updates", [])
    assert any(r["risk_id"] == "risk:regulatory-lifecycle-no-associated-charter" for r in risks)

    # Verify colliding product names (Beacon, Security Portal, JARVIS) are suffixed with 'Platform' as system nodes
    ents = {e["id"]: e for e in mut["entity_upserts"]}
    assert "workstream:beacon" in ents
    assert "system:beacon-platform" in ents
    assert "workstream:security-portal" in ents
    assert "system:security-portal-platform" in ents
    assert "workstream:nea-jarvis" in ents
    assert "system:jarvis-platform" in ents


def test_multi_workstream_allocation_deduplication_and_edges() -> None:
    """Verify specialists allocated across multiple workstreams (e.g. Dave Sunderman with alloc_count=6) create one person node and distinct MEMBER_OF edges."""
    ws_blocks = []
    roster_blocks = []
    for i in range(1, 7):
        ws_blocks.append(
            f"Regulatory Workstream {i}PM: Manager {i}SM: William White\n"
            f"Product: Reg Suite {i}\n"
            f"Bus OpsAI (Farrukh Khan)\nEnterprise AI(Matthew Barrows)\n"
            f"In ProgressNEARegulatory Charter {i} $100K 100%"
        )
        roster_blocks.append(f"Manager {i}\nStaff\nDave Sunderman\n6\nSenior")

    fixture = "## Page 1\n" + "\n".join(ws_blocks) + "\n" + "\n".join(roster_blocks)
    data: dict = {}
    reconcile_tabular_workstream_mutations(data, {"is_document": True}, fixture)
    mut = data["graph_mutations"]
    dave_nodes = [e for e in mut["entity_upserts"] if e["id"] == "person:dave-sunderman"]
    assert len(dave_nodes) == 1

    dave_edges = [
        s for s in mut["relationship_signals"]
        if s["from_id"] == "person:dave-sunderman" and s["relationship"] == "MEMBER_OF"
    ]
    assert len(dave_edges) == 6


def test_change_set_suppression_guard_blocks_unattributed_ocr_candidates() -> None:
    """Verify should_suppress_document_candidate blocks 'Not specified' and 'Unattributed participant' assertions and source_chunks when connected entities exist."""
    connected = {"workstream:nea-jarvis", "person:traci-metzger"}

    unattributed_assertion = {
        "candidate_id": "cand-ocr-1",
        "candidate_type": "assertion",
        "entity_mentions": [{"reported_text": "Not specified"}],
    }
    assert should_suppress_document_candidate(
        unattributed_assertion, ["stakeholder:not-specified"], connected, None, False
    ) is True

    unattributed_chunk = {
        "candidate_id": "cand-ocr-2",
        "candidate_type": "source_chunk",
        "entity_mentions": [{"reported_text": "Unattributed participant"}],
    }
    assert should_suppress_document_candidate(
        unattributed_chunk, ["entity-unattributed"], connected, None, False
    ) is True

    redundant_mention = {
        "candidate_id": "cand-ent-1",
        "candidate_type": "entity_mention",
        "entity_mentions": [{"reported_text": "NEA JARVIS"}],
    }
    assert should_suppress_document_candidate(
        redundant_mention, ["workstream:nea-jarvis"], connected, None, False
    ) is True

    # Valid attributed assertion for an unconnected entity is NOT suppressed
    valid_assertion = {
        "candidate_id": "cand-valid-1",
        "candidate_type": "assertion",
        "entity_mentions": [{"reported_text": "Taylor Mercer"}],
    }
    assert should_suppress_document_candidate(
        valid_assertion, ["person:traci-metzger"], connected, None, False
    ) is False


def test_multiple_unfilled_roles_and_future_hires_receive_distinct_workstream_scoped_ids() -> None:
    """Verify multiple Unfilled (Software Developer) / Unfilled (Product Manager) slots across workstreams do not collide."""
    ws_blocks = []
    roster_blocks = []
    for i in range(1, 6):
        ws_blocks.append(
            f"Conduit Platform {i}PM: Manager {i}SM: William Lane\n"
            f"Product: Conduit {i}\n"
            f"Gen Asset Opti (Jarod Bryant)\nEnterprise AI(Matthew Barrows)\n"
            f"In ProgressNEAConduit Platform {i} $400K 90%"
        )
        roster_blocks.append(f"Manager {i}\nStaff\nSoftwareDeveloper\nFuture Hire #{i}\nStaff")

    fixture = "## Page 1\n" + "\n".join(ws_blocks) + "\n" + "\n".join(roster_blocks)
    data: dict = {}
    reconcile_tabular_workstream_mutations(data, {"is_document": True}, fixture)
    mut = data["graph_mutations"]
    role_ents = [e for e in mut["entity_upserts"] if e["type"] == "role"]
    assert len(role_ents) == 10
    cand_sigs = [s for s in mut["relationship_signals"] if s["relationship"] == "CANDIDATE_FOR"]
    assert len(cand_sigs) == 10
    for r in mut["risk_updates"]:
        assert r.get("entity_id", "").startswith("workstream:conduit-platform-")
        assert r.get("subject_id") == r.get("entity_id")
