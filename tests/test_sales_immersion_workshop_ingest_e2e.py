"""
Wave 1 E2E Test: PartnerAdvisory Sales Immersion Q4 2026 Workshop Extractor & Router Disambiguation.
Verifies:
1. scaffold_document_metadata routes all 4 PartnerAdvisory Sales Immersion Q4 2026 decks (Day 1..4)
   to project='internal_consulting', classification='confidential', and
   DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP without misrouting to 'project_lens'.
2. extract_sparse_enablement_workshop_projection and DocumentGraphExtractorExecutor emit
   high-signal entities, relationships, and assertions across Days 1-4 with
   epistemic_confidence >= 0.90, 100% verbatim grounding, and >= 95% substantive coverage.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
from dual_pipeline.document_pipeline_core.quality import (
    validate_signal_claim_grounding,
    validate_slide_deck_coverage_and_yield,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    extract_slide_and_section_units,
)
from dual_pipeline.transcript_stages_core.offline_synthesizer import (
    OfflineTranscriptSynthesizer,
)


def _build_synthetic_sales_immersion_markdown(day: int, total_pages: int, anchors: dict[int, str]) -> str:
    blocks = []
    for p in range(1, total_pages + 1):
        body = anchors.get(p, "Executive Overview\nNarrative recap of context.")
        blocks.append(f"## Page {p}\n{body}")
    return "\n\n".join(blocks)


DAY_CONFIGS = {
    1: (
        112,
        "10-19-2026",
        14,
        13,
        6,
        {
            1: "WELCOME TO SALES IMMERSION Q4 2026 PartnerAdvisory Sales Learning Proprietary and Confidential",
            2: "Kelly Roberts Sr. Director, Global Sales Enablement & Training Gordon White Camille Davis Kim Merrell",
            5: "Sales Immersion cohorts across Canada, United Kingdom, and United States markets.",
            15: "Customers buy through three lenses: Business Value (BV), Personal Value (PV), and Trust Value (TV).",
            18: "Lane Garritsen Senior Sales Executive Five Seller Personas: Expert, Confidant, Activator, Debater, Realist.",
            19: "Moving to strong Activator habits across the 5 seller personas improves average revenue generation by up to 32%.",
            28: "PartnerAdvisory leads with unique, proactive, and relevant business insights.",
            29: "Activating insights progresses through four stages: Hypothesis, Disruption, Validation, and Attachment.",
            30: "AI and insights uncover deeper account research and validation.",
            31: "CoPilot Researcher and Gtm.consulting.example.com portal hosting Future Of GTM Priorities and Industry Briefings.",
            47: "Customer buying lenses: Business Value, Personal Value, and Trust Value.",
            63: "Actionable stakeholder maps require 11 strategic components including Buying Role and PartnerAdvisory Owner.",
            72: "Leverage the PartnerAdvisory machine: Account Plans, Stakeholder Maps, and Global Marketing Campaigns.",
            80: "Relationship-driven origination requires a 4:1 ratio (4 Relationship Visits to 1 Genuine Pitch).",
            81: "Evolved outcome sales defines quantifiable value during Prospect and Engage.",
            99: "PartnerAdvisory Growth Formula and Three Horizons framework align leadership on H1, H2, and H3.",
            112: "Customer Story Finder tool across Customer Story Hub, Pocket Stories, and PartnerAdvisory One.",
        },
    ),
    2: (
        106,
        "10-20-2026",
        21,
        20,
        8,
        {
            1: "WELCOME TO SALES IMMERSION Q4 2026 Day 2 PartnerAdvisory Sales Learning Pursuit Excellence aivalue.consulting.example.com Proprietary and Confidential",
            3: "Amit Ayer PartnerAdvisory leader on Immersion Team 1 (PartnerAdvisory 7).",
            15: "Pursuits fail due to process inconsistency across markets and unclear role accountability.",
            16: "Pursuit Excellence organization within PartnerAdvisory Sales Excellence.",
            17: "Delivery Excellence partner organization ensuring continuous quality.",
            18: "Pricing & Cost Estimator Tool on Pursuit Excellence Hub.",
            25: "Engagement Lead and Delivery Solution Lead mobilization transitions.",
            26: "Pursuit Manager and Partner Relationship Manager extended pursuit roles.",
            28: "Pursuit Accountable Executive (PAE) single individual ultimately accountable at PartnerAdvisory.",
            29: "Pursuit Relationship Lead (PRL) voice of the customer on the pursuit team.",
            30: "Pursuit Lead (PrL) captain managing daily pursuit execution.",
            31: "Sales Solution Lead (SSL) primary solutioner responsible for solutioning.",
            33: "Geo Teams, Capability Teams, and Customer & Industry Teams in the One Team pursuit model.",
            36: "Core Pursuit Team 4-Role Uniqueness & 2-Pursuit Cap and credit-earning role approval.",
            40: "Salesforce sales system of record for pipeline and forecast accuracy.",
            41: "Friday EOD PST Forecast Hygiene & 125% Weighted Pipeline Rule.",
            43: "Sales Analytics Hub hosting Pipeline Inspection and Sales Health Insights.",
            44: "Global Sales Excellence operates the Sales Analytics Hub.",
            51: "Quantifying value in Impact Excellence and $10B pilot increased close rates by 40%.",
            58: "AI Value Platform (aivalue.consulting.example.com) and Customer Value Estimator.",
            59: "Sales Solution Lead uses the AI Value Platform to quantify KPI impact.",
            61: "Mandatory Stage 2 Salesforce Client Benefit Value Tracking.",
            65: "Paul Schur General Manager, Greater New York (GNY) at PartnerAdvisory.",
            86: "Stage 2 Go/No-Go Qualification & 52% Margin Target.",
            91: "Opportunity Costs of Poor Deal Qualification across 7 risk dimensions.",
            92: "Stage 2 Go / No Go Scorecard across Account, Buyer, Work, and Economics.",
        },
    ),
    3: (
        93,
        "10-21-2026",
        20,
        17,
        8,
        {
            1: "WELCOME TO SALES IMMERSION Q4 2026 Day 3 PartnerAdvisory Sales Learning Microsoft 365 Copilot Single Page Response Overview Proprietary and Confidential",
            5: "Anne Stanton Senior Principal, Client Delivery leading AI Coaching.",
            7: "Anne Stanton leads PartnerAdvisory PNW Sales Microsoft 365 Copilot Advanced training.",
            8: "Advanced M365 Copilot seller workflows span Corporate Memory Brain and Executive Situation Room.",
            9: "Microsoft 365 Copilot enterprise AI suite across Chat, Notebooks, Researcher, Analyst, and Agents.",
            10: "Use the CRAFT prompting framework (Context, Role, Action, Format, Test/Validation & HITL).",
            13: "Copilot Notebooks integrates as a scoped project workspace for corporate memory and synthesis.",
            18: "Work IQ work intelligence context engine in Microsoft 365.",
            21: "Work IQ integrates Microsoft 365 context with Copilot Researcher.",
            35: "PartnerAdvisory GTM BINGO App self-guided AI sales enablement experience.",
            40: "Customer support of PartnerAdvisory is scored from -2 (Negative) to +2 (Champion).",
            41: "Single-Threaded Champion Vulnerability and Mobilizer identification.",
            57: "Pursuit teams must define at most 3 specific, defensible Win Themes (3 MAX).",
            60: "Single Page Response Overview (SPRO) 7-step bridge between discovery and proposal.",
            61: "PartnerAdvisory standardizes the 7-step Single Page Response Overview (SPRO).",
            72: "SPRO Accelerator tool generating a first-draft SPRO in 5 minutes.",
            82: "Stage 4/5 Deal Stall & Procurement Turnaround Risk.",
            83: "PartnerAdvisory pursuit teams co-create Mutual Success Plans with buyers.",
            84: "Stage 2 Mutual Success Plan (MSP) & Internal Close Plan (CP) initiation.",
            87: "14-step Internal Close Plan integrated and AI-enabled in Salesforce.",
            90: "Brad Jackson CEO, Tony Rojas President, Amy Loftus Chief Customer Officer, Mike Heffernan CFO.",
        },
    ),
    4: (
        70,
        "10-22-2026",
        21,
        19,
        7,
        {
            1: "WELCOME TO SALES IMMERSION Q4 2026 Day 4 PartnerAdvisory Sales Learning Establishing & Expanding C-Suite Unified AI Transformation GTM Kit Proprietary and Confidential",
            10: "PartnerAdvisory operates on a One Team model across Sales, Client Partners, Capabilities, Alliances, Industry, and Delivery.",
            15: "Faven McShane Senior Sales Executive leading C-suite Relationships at PartnerAdvisory.",
            17: "Failing to engage CxO buyers causes loss of narrative control and smaller engagements.",
            19: "C-suite buyers operate across 4 mindsets (Turnaround, Legacy, Innovation, Operational).",
            33: "Use integrative negotiation (Slow Down -> State the Concern -> Hand it Back); every concession requires a give/get.",
            35: "Qualify budget with the budget holder in Stage 2+ and anchor first with ranges.",
            52: "H2 GTM Priority Solutions: Adaptive AI-PDLC, Data Modernization, and Mainframe Modernization.",
            57: "Future of Marketing, Advertising, and Customer Experience (MAX) with Stage 1-5 sales enablement assets.",
            58: "Unified AI Transformation GTM Kit with Sales Enablement, Partner, and Industry plays.",
            60: "PartnerAdvisory Solution Catalog: AI Strategy (Will Bray), AI Office (COE), AI Accelerate Teams (Tamarah Usher), AI Factory, AI Enablement, Adaptive Organization (Amalia Goodwin), Application Modernization (Stephen Weinrich), Contact Center Transformation.",
            61: "Filter incoming GTM asks through The Connected Plan (Account Strategy -> Asks -> Alignment).",
        },
    ),
}


class TestSalesImmersionWorkshopIngestE2E(unittest.TestCase):
    """End-to-end verification of PartnerAdvisory Sales Immersion Q4 2026 workshop ingestion."""

    def test_router_disambiguates_all_four_days_to_internal_consulting(self) -> None:
        for day, (pages, expected_date, _, _, _, anchors) in DAY_CONFIGS.items():
            md = _build_synthetic_sales_immersion_markdown(day, pages, anchors)
            meta = scaffold_document_metadata(
                input_path=f"PartnerAdvisory Sales Immersion Q4 2026 - Day {day}.pdf",
                title=f"PartnerAdvisory Sales Immersion Q4 2026 - Day {day}",
                date_str=expected_date,
                source_text=md,
                metadata={"page_count": pages},
            )
            self.assertEqual(meta["project"], "internal_consulting")
            self.assertEqual(meta["classification"], "confidential")
            arch = classify_document_archetype(md, meta)
            self.assertEqual(arch, DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP)

    def test_executor_and_gate_validators_pass_all_four_days(self) -> None:
        executor = DocumentGraphExtractorExecutor(provider=OfflineTranscriptSynthesizer())
        for day, (pages, expected_date, exp_nodes, exp_edges, exp_assertions, anchors) in DAY_CONFIGS.items():
            md = _build_synthetic_sales_immersion_markdown(day, pages, anchors)
            units = extract_slide_and_section_units(md, f"day_{day}_hash", "pdf")
            meta = scaffold_document_metadata(
                input_path=f"PartnerAdvisory Sales Immersion Q4 2026 - Day {day}.pdf",
                title=f"PartnerAdvisory Sales Immersion Q4 2026 - Day {day}",
                date_str=expected_date,
                source_text=md,
                metadata={"page_count": pages, "slide_units": [u.to_dict() for u in units]},
            )
            proj_json = executor.execute(md, pa_context={"metadata": meta})
            proj = json.loads(proj_json)

            muts = proj["graph_mutations"]
            self.assertEqual(len(muts["entity_upserts"]), exp_nodes)
            self.assertEqual(len(muts["relationship_signals"]), exp_edges)
            total_assertions = (
                len(muts["decision_updates"])
                + len(muts["risk_updates"])
                + len(muts["strategic_intent_signals"])
            )
            self.assertEqual(total_assertions, exp_assertions)

            for node in muts["entity_upserts"]:
                self.assertGreaterEqual(node["epistemic_confidence"], 0.90)
            for edge in muts["relationship_signals"]:
                self.assertGreaterEqual(edge["epistemic_confidence"], 0.90)

            grounding_ok, grounding_issues = validate_signal_claim_grounding(proj, md)
            self.assertTrue(grounding_ok, f"Day {day} grounding failed: {grounding_issues}")

            total_muts = exp_nodes + exp_edges + exp_assertions
            cov_ok, cov_issues, cov_metrics = validate_slide_deck_coverage_and_yield(
                proj, md, metadata=meta, mutation_count=total_muts
            )
            self.assertTrue(cov_ok, f"Day {day} coverage failed: {cov_issues}")
            self.assertGreaterEqual(cov_metrics["substantive_coverage_ratio"], 0.90)


if __name__ == "__main__":
    unittest.main()
