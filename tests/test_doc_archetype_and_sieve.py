"""
Unit tests for Wave 1: 8-Archetype Document Classifier, 12-Class Unit Materiality & Quarantine Sieve,
and Visual Ghost-Layer & Master Boilerplate Stripper.
"""
from __future__ import annotations

import unittest

from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.format_source_units import generate_document_structural_index
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    CORE_UNIT_CLASSES,
    QUARANTINED_UNIT_CLASSES,
    UnitDispositionClass,
    classify_unit_sieve,
    extract_slide_and_section_units,
    strip_master_boilerplate_and_footers,
)
from dual_pipeline.pdf_source import normalize_pdf_page_content, purge_occluded_ghost_layers


class TestDocArchetypeAndSieve(unittest.TestCase):
    """Verify all 8 document archetypes, 12 unit disposition classes, and ghost-layer stripping."""

    def test_all_eight_archetypes_classification(self) -> None:
        # 1. TABULAR_PORTFOLIO_EXPORT
        doc1 = "\n".join(
            [f"Workstream {i} PM: Person {i} SM: Master {i}" for i in range(6)]
            + ["https://planning.apex_globalanalytics.com"]
        )
        self.assertEqual(
            classify_document_archetype(doc1),
            DocumentArchetype.TABULAR_PORTFOLIO_EXPORT,
        )

        # 2. STRATEGIC_OFFERING_PLAYBOOK
        doc2 = "## Slide 1: Agentic Managed Services (AMS 101)\nAnatomy of AMS deals and PartnerAdvisory Traverse architecture."
        self.assertEqual(
            classify_document_archetype(doc2),
            DocumentArchetype.STRATEGIC_OFFERING_PLAYBOOK,
        )

        # 3. SPARSE_ENABLEMENT_WORKSHOP
        doc3 = (
            "## Slide 1: Sales Immersion\n"
            "## Slide 2: Discussion (Document)\n"
            "## Slide 3: Let's Play a Game\n"
            "## Slide 4: Small Group Dinners"
        )
        self.assertEqual(
            classify_document_archetype(doc3),
            DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP,
        )

        # 4. PURSUIT_ORALS_PROPOSAL
        doc4 = (
            "## Slide 1: RFP Response - Infrastructure Automation Factory\n"
            "Two Fixed-Price Paths for Segment A and Segment B."
        )
        self.assertEqual(
            classify_document_archetype(doc4),
            DocumentArchetype.PURSUIT_ORALS_PROPOSAL,
        )

        # 5. CONTRACTUAL_SOW_PROSE
        doc5 = (
            "STATEMENT OF WORK\n"
            "Negotiated Purchaser Terms. Estimated Service Fees: $618,390. "
            "2025 Rebate Credit: $165,310. Hourly Rate card attached."
        )
        self.assertEqual(
            classify_document_archetype(doc5),
            DocumentArchetype.CONTRACTUAL_SOW_PROSE,
        )

        # 6. HYBRID_PORTFOLIO_AND_TEMPLATE_DECK
        doc6 = (
            "## Slide 1: 2027 Account Planning Playbook\n"
            "## Slide 11: Geo-POD: WPB, Orl, Jax\n"
            "## Slide 50: READ ME THEN DELETE - [Account Name]"
        )
        self.assertEqual(
            classify_document_archetype(doc6),
            DocumentArchetype.HYBRID_PORTFOLIO_AND_TEMPLATE_DECK,
        )

        # 7. POPULATED_ACCOUNT_PLAN_DASHBOARD
        doc7 = (
            "## Page 1\nPlan Summary: 2027 Apex Global Enterprises\n"
            "Multi-Year Revenue: $24M / $38M / ~$45M\n"
            "Business Unit Buyers: NEA $12M, EDO $7M\n"
            "## Page 2\nPlan Summary by: Capability & GTM Priority Solutions"
        )
        self.assertEqual(
            classify_document_archetype(doc7),
            DocumentArchetype.POPULATED_ACCOUNT_PLAN_DASHBOARD,
        )

        # 8a. VISUAL_TOPOLOGY_AND_ORG_CHART
        doc8a = "Apex Global Analytics Org Chart\nSearch people... Tree Radial Reset\nKirk Cross -> Rich Argentieri"
        self.assertEqual(
            classify_document_archetype(doc8a),
            DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART,
        )

        # 8b. FINANCIAL_STAFFING_WORKBOOK
        doc8b = "Global Pricing v2.0\nStaffing Plan\nRack Rate Revenue\nMandatory Cost Contingency\nTBD1"
        self.assertEqual(
            classify_document_archetype(doc8b),
            DocumentArchetype.FINANCIAL_STAFFING_WORKBOOK,
        )

    def test_twelve_class_unit_sieve_taxonomy(self) -> None:
        # C1: CORE_PORTFOLIO_OR_POD_MATRIX
        cls, _, is_sub = classify_unit_sieve(
            "Geo-POD: WPB, Orl, Jax",
            "Geo-POD: WPB | Account: Apex Global Enterprises | Buyer: NEA $12M | PM: Reid Schenk | SM: Chris Nunalee",
        )
        self.assertEqual(cls, UnitDispositionClass.C1_CORE_PORTFOLIO_OR_POD_MATRIX.value)
        self.assertTrue(is_sub)

        # C2: CORE_ORG_ROSTER_AND_VISUAL_TREE
        cls, _, is_sub = classify_unit_sieve(
            "Representative Team Profiles & Org Chart",
            "Reporting to Rich Argentieri: Matthew Barrows, Alex Kirsch, Chris Nunalee. Authorized Signatories.",
        )
        self.assertEqual(cls, UnitDispositionClass.C2_CORE_ORG_ROSTER_AND_VISUAL_TREE.value)
        self.assertTrue(is_sub)

        # C3: CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING
        cls, _, is_sub = classify_unit_sieve(
            "Commercial Pricing & Statement of Work",
            "Estimated Service Fees $618,390 with Rebate Credit $165,310 and Fixed Fee options for TBD1.",
        )
        self.assertEqual(cls, UnitDispositionClass.C3_CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING.value)
        self.assertTrue(is_sub)

        # C4: CORE_ARCHITECTURE_ROADMAP_POLICY
        cls, _, is_sub = classify_unit_sieve(
            "Platform Target Architecture & Roadmap",
            "- Deploy multi-agent orchestration across Databricks and cloud regions in Q4 2026.",
        )
        self.assertEqual(cls, UnitDispositionClass.C4_CORE_ARCHITECTURE_ROADMAP_POLICY.value)
        self.assertTrue(is_sub)

        # C5: TACTICAL_UI_OR_PROCESS_CADENCE
        cls, _, is_sub = classify_unit_sieve(
            "CRM UI Click-Path Instructions",
            "CRM UI click-path: How to log in Salesforce and use the room booking calendar.",
        )
        self.assertEqual(cls, UnitDispositionClass.C5_TACTICAL_UI_OR_PROCESS_CADENCE.value)
        self.assertFalse(is_sub)

        # Q1: QUARANTINE_TEMPLATE_SCAFFOLD
        cls, _, is_sub = classify_unit_sieve(
            "Slide 50: READ ME THEN DELETE",
            "Account Plan for [Account Name]. Lorem ipsum dolor sit amet. Target $50M.",
        )
        self.assertEqual(cls, UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value)
        self.assertFalse(is_sub)

        # Q2: QUARANTINE_UNMODIFIED_TEMPLATE_DEFAULT
        cls, _, is_sub = classify_unit_sieve(
            "Template Defaults Only",
            "Avg. Bill Rate ($230/$235) and Margin (48%/50%) only template default.",
        )
        self.assertEqual(cls, UnitDispositionClass.Q2_QUARANTINE_UNMODIFIED_TEMPLATE_DEFAULT.value)
        self.assertFalse(is_sub)

        # Q3: QUARANTINE_WORKBOOK_EMPTY_AND_LOOKUP_GRIDS
        cls, _, is_sub = classify_unit_sieve(
            "Empty Staffing Slots",
            "TBD19 | Total Hours: 0 | L2/L3/L4 Helper | DO NOT DELETE THIS COLUMN",
        )
        self.assertEqual(cls, UnitDispositionClass.Q3_QUARANTINE_WORKBOOK_EMPTY_AND_LOOKUP_GRIDS.value)
        self.assertFalse(is_sub)

        # Q4: QUARANTINE_ILLUSTRATIVE_MOCKUP
        cls, _, is_sub = classify_unit_sieve(
            "Slide 11: Illustrative Experience Demo",
            "ILLUSTRATIVE: Maria Delgado (EV553). All applications, NEECodes, people, and numbers are fictional.",
        )
        self.assertEqual(cls, UnitDispositionClass.Q4_QUARANTINE_ILLUSTRATIVE_MOCKUP.value)
        self.assertFalse(is_sub)

        # Q5: QUARANTINE_LEGAL_BOILERPLATE
        cls, _, is_sub = classify_unit_sieve(
            "Section 8: Standard Terms",
            "Compliance with the Clayton Act, Sherman Act, and Securities and Exchange Commission rules.",
        )
        self.assertEqual(cls, UnitDispositionClass.Q5_QUARANTINE_LEGAL_BOILERPLATE.value)
        self.assertFalse(is_sub)

        # Q6: QUARANTINE_PEDAGOGICAL_AND_PARABLES
        for parable in (
            "Discussion (Document): Let's Play a Game #1",
            "Roger Bannister and the 4-minute mile parable",
            "Maya Angelou quote and Robin Dunbar number",
            "Steven Covey habits and Netflix Horizons",
        ):
            cls, _, is_sub = classify_unit_sieve("Workshop Activity", parable)
            self.assertEqual(cls, UnitDispositionClass.Q6_QUARANTINE_PEDAGOGICAL_AND_PARABLES.value)
            self.assertFalse(is_sub)

        # Q7: QUARANTINE_LOGISTICS_DIVIDER_SOCIAL
        for social in (
            "Break! Be back at 10:30 AM\n© 2026 PartnerAdvisory. All Rights Reserved.",
            "Small Group Dinners: Matsu, Wild Ginger, Purple Cafe, Il Bistro",
            "Agenda\n© 2026 PartnerAdvisory",
        ):
            cls, _, is_sub = classify_unit_sieve("Logistics", social)
            self.assertEqual(cls, UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value)
            self.assertFalse(is_sub)

    def test_sales_immersion_112_slide_promotion_and_quarantine_ratio(self) -> None:
        slides = []
        # 14 substantive slides (C2/C4)
        for i in range(1, 15):
            slides.append(
                f"## Slide {i}: Strategic Account GTM Roadmap {i}\n"
                f"- Target 25% growth across $10M enterprise accounts in 2026.\n"
                f"© 2026 PartnerAdvisory. All Rights Reserved."
            )
        # 65 pedagogical/parable slides (Q6)
        parables = [
            "Discussion (Document): Roleplay Exercise",
            "Let's Play a Game #1: Freaky Friday",
            "Roger Bannister 4-minute mile story",
            "Maya Angelou & Robin Dunbar 150 relationships",
            "Steven Covey & Netflix Horizons framework",
        ]
        for i in range(15, 80):
            p_text = parables[(i - 15) % len(parables)]
            slides.append(
                f"## Slide {i}: Workshop Activity {i}\n{p_text}\n© 2026 PartnerAdvisory. All Rights Reserved. {i}"
            )
        # 33 logistics/break/dinner/divider slides (Q7)
        logistics = [
            "Break! Be back at 2:15 PM",
            "Small Group Dinners: Matsu | Wild Ginger | Purple Cafe | Il Bistro",
            "Pre-Assessment: Scan the QR code",
            "Thank You!",
            "Agenda",
        ]
        for i in range(80, 113):
            l_text = logistics[(i - 80) % len(logistics)]
            slides.append(
                f"## Slide {i}: {l_text}\n© 2026 PartnerAdvisory. All Rights Reserved. {i}"
            )

        deck_md = "\n\n".join(slides)
        units = extract_slide_and_section_units(deck_md, "salesimmersion112", "md")
        self.assertEqual(len(units), 112)

        core_units = [u for u in units if u.metadata["disposition"] in CORE_UNIT_CLASSES]
        quarantined_units = [u for u in units if u.metadata["disposition"] in QUARANTINED_UNIT_CLASSES]

        self.assertLessEqual(len(core_units), 16)
        self.assertGreaterEqual(len(quarantined_units), 96)
        for u in quarantined_units:
            self.assertFalse(u.metadata["is_substantive"])

        idx_table = generate_document_structural_index(units)
        self.assertIn("QUARANTINE_PEDAGOGICAL_AND_PARABLES", idx_table)
        self.assertIn("QUARANTINE_LOGISTICS_DIVIDER_SOCIAL", idx_table)

    def test_fl_account_planning_and_infra_orals_ghost_and_template_quarantine(self) -> None:
        # FL Account Planning p.11 occluded Hertz/Vitas purge
        raw_p11 = (
            "Geo-POD: WPB, Orl, Jax\n"
            "Apex Global Enterprises | $24M Target\n"
            "Hertz | Lucy Bickford | $2M\n"
            "Vitas Healthcare | $1.5M\n"
        )
        cleaned_p11 = normalize_pdf_page_content(raw_p11)
        self.assertIn("Apex Global Enterprises", cleaned_p11)
        self.assertNotIn("Hertz", cleaned_p11)
        self.assertNotIn("Vitas Healthcare", cleaned_p11)

        # Infra Orals divider occluded master-slide text
        raw_divider = (
            "RFP RESPONSE AUG 2026 TRM Lakehouse Implementation on Databricks\n"
            "Infrastructure Automation Transformation\n"
            "Section 2: Technical Approach\n"
            "© 2026 PartnerAdvisory. All Rights Reserved."
        )
        cleaned_div = purge_occluded_ghost_layers(raw_divider, document_title="NEE Infrastructure Automation Orals")
        self.assertNotIn("TRM Lakehouse", cleaned_div)
        self.assertIn("Infrastructure Automation Transformation", cleaned_div)

        # FL Account Planning pp. 50-74 READ ME THEN DELETE -> Q1
        for slide_no in range(50, 75):
            cls, _, is_sub = classify_unit_sieve(
                f"Slide {slide_no}: READ ME THEN DELETE",
                "Account Plan Template for [Account Name]. Lorem ipsum dolor sit amet.",
            )
            self.assertEqual(cls, UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value)
            self.assertFalse(is_sub)

    def test_real_sample_pdfs_ghost_layers_and_sieve_calibration(self) -> None:
        from pathlib import Path
        from dual_pipeline.document_pipeline_core.format_source_units import build_pdf_source_units
        from dual_pipeline.pdf_source import inspect_pdf_pages

        home_dir = Path.home()
        repo_root = next(
            (p for p in Path(__file__).resolve().parents if (p / "inputs" / "documents").exists()),
            Path(__file__).resolve().parents[1],
        )

        # 1. Infra Orals (111 pp.)
        orals_path = home_dir / "Downloads" / "NEE Infrastructure Automation Transformation RFP_Orals.pdf"
        if orals_path.exists():
            rep_orals = inspect_pdf_pages(orals_path.read_bytes(), title=orals_path.name)
            for pg in rep_orals.pages:
                self.assertNotIn(
                    "TRM Lakehouse Implementation on Databricks",
                    pg.text,
                    f"Infra Orals p.{pg.page_num} still contains occluded TRM Lakehouse header",
                )
            u_orals = build_pdf_source_units(rep_orals, "orals111hash")
            sub_orals = [u for u in u_orals if u.metadata["is_substantive"]]
            non_sub_orals = [u for u in u_orals if not u.metadata["is_substantive"]]
            self.assertGreaterEqual(len(sub_orals), 24)
            self.assertLessEqual(len(sub_orals), 42)
            self.assertGreaterEqual(len(non_sub_orals), 65)

        # 2. FL 2027 Account Planning Playbook (74 pp.)
        fl_path = home_dir / "Desktop" / "Florida 2027 Account Planning Playbook.pdf"
        if fl_path.exists():
            rep_fl = inspect_pdf_pages(fl_path.read_bytes(), title=fl_path.name)
            p11_text = rep_fl.pages[10].text
            self.assertIn("Apex Global", p11_text)
            self.assertNotIn("Hertz", p11_text)
            self.assertNotIn("Vitas Healthcare", p11_text)
            u_fl = build_pdf_source_units(rep_fl, "fl74hash")
            for u in u_fl[49:]:
                self.assertFalse(
                    u.metadata["is_substantive"],
                    f"FL Playbook template p.{u.metadata['page_num']} leaked as substantive",
                )
                self.assertEqual(
                    u.metadata["sieve_class"],
                    UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value,
                )

        # 3. Sales Immersion Day 1 (112 pp.)
        si_path = home_dir / "Downloads" / "Sales Immersion Q4 2026 - Day 1 (1).pdf"
        if si_path.exists():
            rep_si = inspect_pdf_pages(si_path.read_bytes(), title=si_path.name)
            u_si = build_pdf_source_units(rep_si, "si112hash")
            sub_si = [u for u in u_si if u.metadata["is_substantive"]]
            quar_si = [u for u in u_si if not u.metadata["is_substantive"]]
            self.assertLessEqual(len(sub_si), 16)
            self.assertGreaterEqual(len(quar_si), 96)

        # 4. AMS 101 Florida (25 pp.)
        ams_path = repo_root / "inputs" / "documents" / "AMS_Training_and_Solution_Materials_AMS_101_Florida.pdf"
        if ams_path.exists():
            rep_ams = inspect_pdf_pages(ams_path.read_bytes(), title=ams_path.name)
            u_ams = build_pdf_source_units(rep_ams, "ams25hash")
            self.assertEqual(
                u_ams[24].metadata["sieve_class"],
                UnitDispositionClass.C5_TACTICAL_UI_OR_PROCESS_CADENCE.value,
            )
            self.assertFalse(u_ams[24].metadata["is_substantive"])

    def test_offer_letter_legal_addenda_sieve_and_fidelity(self) -> None:
        from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
            _restore_document_offer_and_roster_fidelity,
        )
        cls, _, is_sub = classify_unit_sieve(
            "Mutual Arbitration Agreement",
            "Mutual Arbitration Agreement under the Federal Arbitration Act and JAMS Employment Rules.",
        )
        self.assertEqual(cls, UnitDispositionClass.Q5_QUARANTINE_LEGAL_BOILERPLATE.value)
        self.assertFalse(is_sub)

        proj = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:amit-ayer", "type": "person", "name": "Amit Ayer", "description": "Offered Senior Director"},
                    {"id": "person:kristine-rohls", "type": "person", "name": "Kristine Rohls", "description": "Project Stakeholder at PartnerAdvisory."},
                ],
                "relationship_signals": [
                    {"signal_id": "s1", "from_id": "person:amit-ayer", "to_id": "org:partner_advisory", "relationship": "MEMBER_OF", "assertion_scope": "Offer of employment", "epistemic_status": "CONFIRMED", "description": "Member of PartnerAdvisory."},
                    {"signal_id": "s2", "from_id": "person:kristine-rohls", "to_id": "org:partner_advisory", "relationship": "MEMBER_OF", "source": "roster"},
                ],
            }
        }
        _restore_document_offer_and_roster_fidelity(proj)
        self.assertEqual(len(proj["graph_mutations"]["entity_upserts"]), 1)
        self.assertEqual(len(proj["graph_mutations"]["relationship_signals"]), 1)
        self.assertEqual(proj["graph_mutations"]["relationship_signals"][0]["epistemic_status"], "PROPOSED")
        self.assertEqual(proj["graph_mutations"]["relationship_signals"][0]["description"], "Offered employment at PartnerAdvisory.")


if __name__ == "__main__":
    unittest.main()
