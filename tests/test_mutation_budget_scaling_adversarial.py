"""
tests/test_mutation_budget_scaling_adversarial.py

Adversarial and Edge-Case Test Suite for Duration-Scaled Gate 3.10 Mutation Budget
and Baseline Attendee Roster Exemption.
Fulfills Wave 2 acceptance criteria and E2E coverage invariants under
.agents/rules/e2e-and-edge-case-coverage.md.

Covers 4 mandatory categories:
1. Boundary payload: Short (15m) sessions strictly bounded at <=30 mutations; empty payloads.
2. Malformed input / Roster exemption: Attendee MEMBER_OF triples exempt from pruning;
   never crowd out domain systems, relationships, or decisions.
3. Overflow & exhaustion: 105-minute session expands ceiling to 75 and scales sub-caps
   for risks, proposals, and decisions.
4. Exact production regression: August 27, 2026 Chakra Saint Paul 105-minute metadata.
"""
from __future__ import annotations

from typing import Any, Dict, List
import pytest

from dual_pipeline.transcript_duration_helper import resolve_duration_mutation_budget
from dual_pipeline.transcript_qa_core.semantic_linters import lint_mutation_budget
from dual_pipeline.transcript_stages_core.graph_post_extractor import _enforce_mutation_budget
from dual_pipeline.transcript_qa_core.graph_healer import _heal_mutation_budget


def _build_test_projection(
    attendee_count: int = 8,
    domain_entity_count: int = 15,
    domain_signal_count: int = 15,
    decision_count: int = 5,
    risk_count: int = 4,
    proposal_count: int = 4,
) -> Dict[str, Any]:
    """Helper to generate a rich synthetic Output 03 payload."""
    entities: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    evidence_index: Dict[str, Any] = {}

    # 1. Roster attendees
    for i in range(attendee_count):
        pid = f"person:attendee_{i}"
        stmt_id = f"ev-roster-{i}"
        evidence_index[stmt_id] = {
            "statement_id": stmt_id,
            "text": f"Attendee {i} from PartnerAdvisory joined the meeting.",
            "speaker": f"Attendee {i}",
        }
        entities.append({
            "id": pid,
            "type": "person",
            "name": f"Attendee {i}",
            "mutation": "CONFIRM",
            "evidence_ids": [stmt_id],
        })
        signals.append({
            "signal_id": f"sig-roster-{i}",
            "from_id": pid,
            "to_id": "org:partner_advisory",
            "relationship": "MEMBER_OF",
            "evidence_ids": [stmt_id],
        })

    entities.append({
        "id": "org:partner_advisory",
        "type": "org",
        "name": "PartnerAdvisory",
        "mutation": "CONFIRM",
        "evidence_ids": ["ev-roster-0"],
    })

    # 2. Domain technical entities
    for j in range(domain_entity_count):
        sid = f"system:core_service_{j}"
        stmt_id = f"ev-domain-{j}"
        evidence_index[stmt_id] = {
            "statement_id": stmt_id,
            "text": f"Service {j} integrates with cloud data infrastructure.",
            "speaker": "Attendee 0",
        }
        entities.append({
            "id": sid,
            "type": "system",
            "name": f"Core Service {j}",
            "mutation": "CREATE",
            "evidence_ids": [stmt_id],
        })

    # 3. Domain relationship signals
    for k in range(domain_signal_count):
        from_idx = k % domain_entity_count
        to_idx = (k + 1) % domain_entity_count
        signals.append({
            "signal_id": f"sig-domain-{k}",
            "from_id": f"system:core_service_{from_idx}",
            "to_id": f"system:core_service_{to_idx}",
            "relationship": "INTEGRATES_WITH" if k % 2 == 0 else "DEPENDS_ON",
            "evidence_ids": [f"ev-domain-{from_idx}"],
        })

    # 4. Decisions, Risks, Proposals
    decisions = [
        {
            "decision_id": f"decision:arch_choice_{d}",
            "decision_question": f"Approved architecture pattern {d}?",
            "status": "APPROVED",
            "observed_at": "08/27/2026",
            "evidence_ids": ["ev-domain-0"],
        }
        for d in range(decision_count)
    ]

    risks = [
        {
            "risk_id": f"risk:migration_risk_{r}",
            "description": f"Capacity bottleneck observed during migration {r}.",
            "severity": "HIGH",
            "stale_after": "12/31/2026",
            "mutation": "CREATE",
            "evidence_ids": ["ev-domain-0"],
        }
        for r in range(risk_count)
    ]

    proposals = [
        {
            "proposal_id": f"proposal:offshore_pod_{p}",
            "title": f"Proposed pod deployment strategy {p}",
            "rationale": f"Accelerates delivery velocity for pod {p}.",
            "status": "PROPOSED",
            "mutation": "CREATE",
            "evidence_ids": ["ev-domain-0"],
        }
        for p in range(proposal_count)
    ]

    return {
        "schema_version": "5.7",
        "meeting_id": "test-scaling-session",
        "classification": "internal",
        "evidence_index": evidence_index,
        "graph_mutations": {
            "entity_upserts": entities,
            "relationship_signals": signals,
            "decision_updates": decisions,
            "risk_updates": risks,
            "proposal_updates": proposals,
            "action_updates": [],
            "strategic_intent_signals": [],
        },
    }


# ── Category 1: Boundary Payloads (Short Meetings) ────────────────────────────

def test_boundary_short_meeting_mutation_ceiling_bounded_at_30():
    """Verify that a 15-minute meeting duration strictly bounds to max_budget=30."""
    meta = {"duration_minutes": 15, "meeting_duration_minutes": 15}
    ceiling = resolve_duration_mutation_budget(metadata=meta, text="Short 15 minute recap.")
    assert ceiling == 30

    data = _build_test_projection(attendee_count=2, domain_entity_count=3, domain_signal_count=3)
    data["metadata"] = meta

    passed, violations, details = lint_mutation_budget(data, total_mutations=35)
    assert not passed
    assert details["base_budget"] == 30


def test_boundary_empty_and_minimal_projections():
    """Verify empty or zero mutation payloads validate cleanly without division-by-zero or crash."""
    empty_data = {
        "schema_version": "5.7",
        "meeting_id": "empty-meeting",
        "graph_mutations": {},
    }
    passed, violations, details = lint_mutation_budget(empty_data, total_mutations=0)
    assert passed
    assert details["budget_ceiling"] == 30
    assert details["total_mutations"] == 0


# ── Category 2: Roster Exemption Governance ───────────────────────────────────

def test_roster_member_of_triples_exempt_from_pruning_domain_signals():
    """Verify 8 attendee MEMBER_OF triples do not crowd out domain systems under _enforce_mutation_budget."""
    data = _build_test_projection(
        attendee_count=8,
        domain_entity_count=10,
        domain_signal_count=10,
        decision_count=2,
        risk_count=2,
        proposal_count=2,
    )
    muts = data["graph_mutations"]
    initial_signals = list(muts["relationship_signals"])
    initial_entities = list(muts["entity_upserts"])

    # Enforce with short 30 base budget
    _enforce_mutation_budget(muts, max_budget=30, metadata={"duration_minutes": 30})

    # All 8 attendee MEMBER_OF signals must be strictly preserved
    roster_sigs = [s for s in muts["relationship_signals"] if s["relationship"] == "MEMBER_OF"]
    assert len(roster_sigs) == 8

    # Core systems must NOT have been completely eradicated
    core_systems = [e for e in muts["entity_upserts"] if e.get("type") == "system"]
    assert len(core_systems) >= 4, f"Domain systems wiped out! Only {len(core_systems)} remaining."


# ── Category 3: Overflow & Multi-Hour Scaling (105-Minute Meetings) ───────────

def test_overflow_multi_hour_duration_expands_ceiling_to_75():
    """Verify a 105-minute session receives proportional mutation ceiling up to 75."""
    meta = {"duration_minutes": 105, "meeting_duration_minutes": 105}
    ceiling = resolve_duration_mutation_budget(metadata=meta, text="x" * 120000)
    assert ceiling >= 75

    data = _build_test_projection(
        attendee_count=8,
        domain_entity_count=20,
        domain_signal_count=20,
        decision_count=6,
        risk_count=5,
        proposal_count=5,
    )
    data["metadata"] = meta
    total_mutations = (
        len(data["graph_mutations"]["entity_upserts"])
        + len(data["graph_mutations"]["relationship_signals"])
        + len(data["graph_mutations"]["decision_updates"])
        + len(data["graph_mutations"]["risk_updates"])
        + len(data["graph_mutations"]["proposal_updates"])
    )
    # Total mutations = 29 entities + 28 signals + 6 decisions + 5 risks + 5 proposals = 73
    assert total_mutations >= 60

    passed, violations, details = lint_mutation_budget(data, total_mutations=total_mutations)
    assert passed, f"Gate 3.10 failed on 105-minute projection: {violations}"
    assert details["budget_ceiling"] >= 75


def test_overflow_healer_scales_subcaps_proportionally():
    """Verify _heal_mutation_budget scales risk, proposal, and decision sub-caps on 105m meeting."""
    data = _build_test_projection(
        attendee_count=8,
        domain_entity_count=15,
        domain_signal_count=15,
        decision_count=8,
        risk_count=6,
        proposal_count=7,
    )
    muts = data["graph_mutations"]
    changes: List[str] = []

    # Run healer on 105-minute meeting
    _heal_mutation_budget(
        data,
        muts,
        changes,
        max_budget=30,
        metadata={"duration_minutes": 105},
        f1_text="x" * 120000,
    )

    # Verify decisions, risks, proposals were NOT crushed down to static 4/3/4 caps
    assert len(muts["decision_updates"]) >= 6, f"Decisions over-trimmed: {len(muts['decision_updates'])}"
    assert len(muts["risk_updates"]) >= 5, f"Risks over-trimmed: {len(muts['risk_updates'])}"
    assert len(muts["proposal_updates"]) >= 6, f"Proposals over-trimmed: {len(muts['proposal_updates'])}"


# ── Category 4: Exact Production Regression (Chakra 08-27-2026) ───────────────

def test_production_regression_chakra_proportional_retention():
    """Verify exact Chakra 105-minute scenario retains >= 45 grounded mutations without pruning."""
    chakra_meta = {
        "observed_at": "08/27/2026",
        "duration_minutes": 105,
        "meeting_duration_minutes": 105,
        "proportional_payload_budget": True,
        "attendees": [
            {"name": "Mark McCarthy", "org": "PartnerAdvisory"},
            {"name": "Rianne Donst", "org": "PartnerAdvisory"},
            {"name": "Jince Jacob", "org": "PartnerAdvisory"},
            {"name": "Drew Oakley", "org": "PartnerAdvisory"},
            {"name": "Cameron Mercer", "org": "PartnerAdvisory"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory"},
            {"name": "Lori Bartlett", "org": "NEA"},
            {"name": "Chakra Sankaraiah", "org": "NEA"},
        ],
    }
    ceiling = resolve_duration_mutation_budget(metadata=chakra_meta, text="x" * 136189)
    assert ceiling >= 75

    data = _build_test_projection(
        attendee_count=8,
        domain_entity_count=15,
        domain_signal_count=15,
        decision_count=5,
        risk_count=4,
        proposal_count=4,
    )
    data["metadata"] = chakra_meta
    muts = data["graph_mutations"]

    _enforce_mutation_budget(
        muts,
        max_budget=30,
        metadata=chakra_meta,
        text="x" * 136189,
    )

    total_retained = (
        len(muts["entity_upserts"])
        + len(muts["relationship_signals"])
        + len(muts["decision_updates"])
        + len(muts["risk_updates"])
        + len(muts["proposal_updates"])
    )
    assert total_retained >= 45, f"Retained only {total_retained} mutations on 105m Chakra meeting"
    assert "_budget_triage_audit" in muts
    assert muts["_budget_triage_audit"]["budget_ceiling"] >= 75


def test_materiality_short_bcg_partner_advisory_meeting_expands_budget_vs_internal_status():
    """Verify a 24m BCG/PartnerAdvisory/APEX_TRANS architecture meeting expands budget >=60 while a 30m internal status sync stays at 30."""
    from dual_pipeline.transcript_duration_helper import count_roster_scaffolding_mutations
    from dual_pipeline.llm_judge_core.reconciler import CrossArtifactReconciler

    bcg_meta = {
        "meeting_name": "Introduction: PartnerAdvisory / BCG / APEX_TRANS Insights Hub",
        "duration_minutes": 24,
        "attendees": [
            {"name": "Alex Rivera", "org": "APEX_TRANS"},
            {"name": "Marcus Vance", "org": "PartnerAdvisory"},
            {"name": "Anece Elakkari", "org": "PartnerAdvisory"},
            {"name": "Will Booth", "org": "BCG"},
            {"name": "John Snipes", "org": "BCG"},
            {"name": "Ariel Wait", "org": "APEX_TRANS"},
            {"name": "Amit Ayer", "org": "APEX_TRANS"},
        ],
    }
    bcg_text = (
        "Architecture walkthrough of APEX_TRANS Insights Hub prototype integrating PI Historian, "
        "SAP, Maximo, SCADA, EMS, and NERC CIP cybersecurity governance for Lone Star OpCo."
    )
    bcg_budget = resolve_duration_mutation_budget(metadata=bcg_meta, text=bcg_text)
    assert bcg_budget >= 60, f"Expected BCG/PartnerAdvisory 24m materiality budget >= 60, got {bcg_budget}"

    internal_meta = {
        "meeting_name": "Internal Weekly Check-In",
        "duration_minutes": 30,
        "rigor_tier": "TIER_3_LIGHTWEIGHT_STATUS",
        "attendees": [{"name": "Alice", "org": "LENS"}, {"name": "Bob", "org": "LENS"}],
    }
    internal_budget = resolve_duration_mutation_budget(metadata=internal_meta, text="Quick team status check-in.")
    assert internal_budget == 30

    # Verify full roster scaffolding exemption + reconciler pass on 48-mutation BCG projection
    bcg_proj = _build_test_projection(
        attendee_count=7,
        domain_entity_count=12,
        domain_signal_count=12,
        decision_count=3,
        risk_count=2,
        proposal_count=3,
    )
    bcg_proj["metadata"] = bcg_meta
    roster_exempt = count_roster_scaffolding_mutations(bcg_proj["graph_mutations"])
    assert roster_exempt >= 12
    reconciler = CrossArtifactReconciler()
    failures = reconciler.validate_mutation_budget(bcg_proj, metadata=bcg_meta, f1_text=bcg_text)
    assert failures == [], f"Expected zero reconciler budget failures on BCG meeting, got {failures}"


def test_bcg_partner_advisory_x3_attendee_parity_preserves_all_domain_systems():
    """Verify adding Ariel Wait and Amit Ayer satisfies X3 Attendee Parity while preserving Lone Star and all enterprise systems."""
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract

    bcg_meta = {
        "meeting_name": "Introduction: PartnerAdvisory / BCG / APEX_TRANS Insights Hub",
        "duration_minutes": 24,
        "attendees": [
            "Alex Rivera (APEX_TRANS)",
            "Marcus Vance (PartnerAdvisory)",
            "Anece Elakkari (PartnerAdvisory)",
            "Will Booth (BCG)",
            "John Snipes (BCG)",
            "Ariel Wait (APEX_TRANS)",
            "Amit Ayer (PartnerAdvisory)",
        ],
    }
    bcg_text = (
        "Architecture walkthrough of APEX_TRANS Insights Hub prototype integrating PI Historian, "
        "SAP, Maximo, SCADA, EMS, NEA, Nexus, ROCC, and NERC CIP cybersecurity governance for Lone Star OpCo."
    )
    proj = _build_test_projection(
        attendee_count=5,
        domain_entity_count=10,
        domain_signal_count=10,
        decision_count=2,
        risk_count=2,
        proposal_count=3,
    )
    muts = proj["graph_mutations"]
    # Add Ariel Wait, Amit Ayer, and Lone Star Transmission explicitly
    for slug, name, org_id in [
        ("person:mitchell_simmons", "Alex Rivera", "org:apex_trans"),
        ("person:aaron_adams", "Marcus Vance", "org:partner_advisory"),
        ("person:anece_elakkari", "Anece Elakkari", "org:partner_advisory"),
        ("person:will_booth", "Will Booth", "org:bcg"),
        ("person:john_snipes", "John Snipes", "org:bcg"),
        ("person:ariel_wait", "Ariel Wait", "org:apex_trans"),
        ("person:amit_ayer", "Amit Ayer", "org:partner_advisory"),
    ]:
        muts["entity_upserts"].append({"id": slug, "type": "person", "name": name, "mutation": "CONFIRM", "evidence_ids": ["ev-roster-0"]})
        muts["relationship_signals"].append({"signal_id": f"sig-{slug}", "from_id": slug, "to_id": org_id, "relationship": "MEMBER_OF", "evidence_ids": ["ev-roster-0"]})
    for org_id, org_name in [("org:apex_trans", "APEX_TRANS"), ("org:bcg", "BCG"), ("org:lone-star-transmission", "Lone Star Transmission")]:
        muts["entity_upserts"].append({"id": org_id, "type": "org", "name": org_name, "mutation": "CONFIRM", "evidence_ids": ["ev-domain-0"]})
    muts["relationship_signals"].append({
        "signal_id": "sig-lone-star-pilot",
        "from_id": "system:core_service_0",
        "to_id": "org:lone-star-transmission",
        "relationship": "DELIVERS",
        "evidence_ids": ["ev-domain-0"],
    })

    changes: List[str] = []
    _enforce_mutation_budget(muts, max_budget=30, metadata=bcg_meta, text=bcg_text)
    _heal_mutation_budget(proj, muts, changes, max_budget=30, metadata=bcg_meta, f1_text=bcg_text)

    surviving_ids = {e["id"] for e in muts["entity_upserts"]}
    assert "person:ariel_wait" in surviving_ids
    assert "person:amit_ayer" in surviving_ids
    assert "org:lone-star-transmission" in surviving_ids

    x3_passed, x3_errors = MeetingXExitContract.validate_x3_cross_output_coherence(bcg_text, "# Recap", proj, bcg_meta)
    parity_errors = [e for e in x3_errors if "Attendee Parity" in e]
    assert parity_errors == [], f"Unexpected X3 Attendee Parity errors: {parity_errors}"


def test_tabular_workstream_roster_document_exemption_and_extraction():
    """Verify multi-workstream tabular planning PDFs extract all workstreams and roster assignments without budget truncation."""
    from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
        is_tabular_workstream_roster_document,
        reconcile_tabular_workstream_mutations,
    )

    sample_doc = "\n".join(
        [
            "## Page 1",
            *[
                f"Workstream Alpha {i}PM: Lead Person {i}SM: Scrum Master {i}\n"
                f"Product: Core Platform {i}\n"
                f"TechSales (Reid Schenk)\n"
                f"NEA Operations (Chris Nunalee)\n"
                f"In Progress $1,200K 100%"
                for i in range(1, 7)
            ],
            *[
                f"Lead Person {i}\nSenior\nEngineer {i}\nContractor"
                for i in range(1, 7)
            ],
        ]
    )
    assert is_tabular_workstream_roster_document(sample_doc)
    data = {
        "schema_version": "1.0.0",
        "meeting_id": "document-nea-planning",
        "is_document": True,
        "output3_only": True,
        "evidence_index": {},
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        "evidence_only_statement_ids": [],
    }
    changes = reconcile_tabular_workstream_mutations(data, {"is_document": True}, sample_doc)
    assert changes
    ents = data["graph_mutations"]["entity_upserts"]
    sigs = data["graph_mutations"]["relationship_signals"]
    ws_ids = [e["id"] for e in ents if e["id"].startswith("workstream:")]
    assert len(ws_ids) == 6
    total_m = len(ents) + len(sigs)
    passed, violations, _ = lint_mutation_budget(
        data, total_mutations=total_m, f1_text=sample_doc, metadata={"is_document": True, "output3_only": True}
    )
    assert passed, f"Tabular workstream roster failed Gate 3.10: {violations}"



