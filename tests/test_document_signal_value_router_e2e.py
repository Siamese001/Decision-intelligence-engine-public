"""
End-to-end tests for Wave 1: Intelligent Document Value Scorer & Exhaustive Multi-Unit Extraction Router.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_and_route_document_extraction,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
from dual_pipeline.document_pipeline_core.signal_value_router import (
    ExtractionMode,
    PerspectiveOrigin,
    SignalTier,
     apply_signal_profile_to_metadata,
    assess_document_signal_profile,
)
from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer


NEA_Q4_PLANNING_DASHBOARD_FIXTURE = """## Page 1
Team Assignments - NEA Planning 10/20/26, 5:21 PM
https://planning.apex_globalanalytics.com/quarterly-planning/assignments?quarterId=3 Page 1 of 21
2026 Q4 - Active Dashboard Missions Supply & Demand Workstreams Assignments
WORKSTREAMS 90 REQUESTED PEOPLE 404.9 ASSIGNABLE PEOPLE 376 ASSIGNMENTS 425 PERCENT ASSIGNED 95%
UNFILLED FTE 30.4 of 404.9 UNASSIGNED PEOPLE 42 of 376
All Workstream Assignments (90)
Workstream Vertical Department Status Missions Est. NAV ($K) Team Assignments Assignment Progress

5. Forge.Customer
PM: Andrew Turvey
SM: Kaleena McHugo
Product: 5. Forge.Origination
Tech Sales (Reid Schenk)
NEA Operations (Chris Nunalee)
In Progress
NEA 5. Forge.Customer
Resource Note: Required FTE
$2,442K 100%
Andrew Turvey Senior
Andy Gaskell Contractor
Pete Klein Staff

Adoption Metrics
PM: Divya Vedula
SM: William Lane
Product: Astra
Enterprise AI (Anuj Chokshi)
Enterprise AI (Matthew Barrows)
In Progress
NEA Adoption Metrics
$546K 100%
Divya Vedula Senior
Akshay Nair Contractor

AI FinOps Spend Visibility & Control — Q4 '26
PM: Jince Jacob
SM: William Lane
Product: AI FinOps Dashboard
Enterprise AI (Anuj Chokshi)
Enterprise AI (Matthew Barrows)
Proposed
NEA AI FinOps Spend Visibility & Control — Q4 '26
$951K 75%
Jince Jacob Staff
Hari Chittaluru Senior

## Page 2
AI Governance
PM: Divya Vedula
SM: William Lane
Product: Astra
Enterprise AI (Anuj Chokshi)
Enterprise AI (Matthew Barrows)
In Progress
NEA Astra AI Governance Capabilities
$527K 89%

Approvals Agentic Reviews
PM: Jenish Kafle
SM: Kaleena McHugo
Product: 3. Forge.Navigator
Tech Sales (Reid Schenk)
NEA Operations (Chris Nunalee)
In Progress
NEA Approvals Agentic Reviews
$2,861K 100%

JARVIS
PM: Taylor Mercer
SM: Kaleena McHugo
Product: JARVIS
Tech Sales (Reid Schenk)
NEA Operations (Chris Nunalee)
In Progress
NEA JARVIS
$284K 100%
Taylor Mercer Contractor
Jason Lickliter Contractor
Mitch Garstka Contractor
"""

CONTRACTUAL_SOW_FIXTURE = """## Page 1
STATEMENT OF WORK (SOW)
Apex Global Enterprises & PartnerAdvisory Consulting — ENTERPRISE4U Align & Position Request Form (PRF) Automation
Negotiated Purchaser Terms: Fixed Fee Engagement
Estimated Service Fees: $1,250,000 | Hourly Rate Card & Rebate Credit Schedule
Client Responsibility: Apex Global Enterprises IT and HR360 Product Ownership.
PartnerAdvisory Responsibility: Deliver end-to-end Position Request Form workflow and SAP integration.
"""

SPARSE_WORKSHOP_FIXTURE = """## Slide 1
PartnerAdvisory Sales Immersion Day 1
Let's play a game! Freaky Friday Icebreaker
Small group dinners at 6:30 PM.
Pre-assessment and Post-assessment activity: draft your 30-second pitch.
Break! Be back at 2:15 PM for Stand & Deliver.
"""


class _StubPortfolioSynthesizer(OfflineTranscriptSynthesizer):
    """Deterministic offline synthesizer returning valid base schema for Stage 03."""

    def synthesize_graph(self, system_prompt: str, user_content: str, **kwargs: Any) -> str:
        payload = {
            "schema_version": "5.7",
            "meeting_date": "10/20/2026",
            "meeting_archetype": "DOCUMENT_MODE",
            "is_document": True,
            "evidence_index": {
                "ev-init-1": {
                    "speaker": "NEA Planning Portal",
                    "source_classification": "FACT",
                    "text": "5. Forge.Customer PM: Andrew Turvey SM: Kaleena McHugo",
                    "exact_text": "5. Forge.Customer\nPM: Andrew Turvey\nSM: Kaleena McHugo",
                    "locator": {"kind": "slide", "value": "p1"},
                    "statement_id": "ev-init-1",
                    "kind": "technical",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "workstream:5-forge-customer",
                        "type": "workstream",
                        "name": "5. Forge.Customer",
                        "description": "NEA Q4 workstream 5. Forge.Customer.",
                        "mutation": "CREATE",
                        "evidence_ids": ["ev-init-1"],
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "risk_updates": [],
                "proposal_updates": [],
                "action_updates": [],
            },
        }
        return json.dumps(payload)


def test_nea_q4_planning_dashboard_scores_high_signal_authoritative() -> None:
    profile = assess_document_signal_profile(NEA_Q4_PLANNING_DASHBOARD_FIXTURE)
    assert profile.archetype == DocumentArchetype.TABULAR_PORTFOLIO_EXPORT.value
    assert profile.signal_tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert profile.strategic_value_score >= 0.90
    assert profile.perspective_origin == PerspectiveOrigin.CLIENT_INTERNAL_NEA.value
    assert profile.recommended_extraction_mode == ExtractionMode.EXHAUSTIVE_PORTFOLIO_AND_ROSTER.value
    assert profile.requires_graph_delta_hitl is True
    assert profile.dynamic_mutation_budget >= 300
    assert profile.page_count == 21
    assert profile.workstream_density_count >= 6
    assert profile.financial_signal_count >= 6


def test_contractual_sow_routes_to_joint_client_partner_exhaustive_mode() -> None:
    profile = assess_document_signal_profile(CONTRACTUAL_SOW_FIXTURE)
    assert profile.archetype == DocumentArchetype.CONTRACTUAL_SOW_PROSE.value
    assert profile.signal_tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert profile.perspective_origin == PerspectiveOrigin.JOINT_CLIENT_PARTNER.value
    assert profile.recommended_extraction_mode == ExtractionMode.EXHAUSTIVE_STRUCTURAL_EXTRACTION.value
    assert profile.requires_graph_delta_hitl is True
    assert profile.dynamic_mutation_budget >= 90


def test_sparse_workshop_deck_remains_bounded() -> None:
    profile = assess_document_signal_profile(SPARSE_WORKSHOP_FIXTURE)
    assert profile.archetype == DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP.value
    assert profile.signal_tier == SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE.value
    assert profile.recommended_extraction_mode == ExtractionMode.BOUNDED_WORKSHOP_EXTRACTION.value
    assert profile.requires_graph_delta_hitl is False
    assert profile.dynamic_mutation_budget <= 40


def test_executor_wires_signal_profile_and_bypasses_meeting_cap_e2e() -> None:
    provider = _StubPortfolioSynthesizer()
    executor = DocumentGraphExtractorExecutor(provider)
    meta: Dict[str, Any] = {
        "title": "Team Assignments - NEA Planning",
        "date": "10/20/2026",
        "project": "nea",
    }
    output_json = executor.execute(
        NEA_Q4_PLANNING_DASHBOARD_FIXTURE,
        pa_context={"metadata": meta},
    )
    parsed = json.loads(output_json)
    assert "graph_mutations" in parsed
    assert meta.get("signal_tier") == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert meta.get("perspective_origin") == PerspectiveOrigin.CLIENT_INTERNAL_NEA.value
    assert meta.get("recommended_extraction_mode") == ExtractionMode.EXHAUSTIVE_PORTFOLIO_AND_ROSTER.value
    assert meta.get("requires_graph_delta_hitl") is True
    assert int(meta.get("max_mutation_budget", 0)) >= 300

    route_dict = classify_and_route_document_extraction(NEA_Q4_PLANNING_DASHBOARD_FIXTURE, {})
    assert route_dict["signal_tier"] == SignalTier.HIGH_SIGNAL_AUTHORITATIVE.value
    assert classify_document_archetype(NEA_Q4_PLANNING_DASHBOARD_FIXTURE) == DocumentArchetype.TABULAR_PORTFOLIO_EXPORT
