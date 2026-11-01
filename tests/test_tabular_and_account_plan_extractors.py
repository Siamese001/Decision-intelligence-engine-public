"""
Unit tests for Wave 2: Tabular Portfolio Stitcher, Geo-POD Matrix & 6-Zone Account Plan Extractor.
"""
from __future__ import annotations

import unittest

from dual_pipeline.document_pipeline_core.account_plan_extractor import (
    expand_abbreviated_person_name,
    extract_account_plan_projection,
    parse_geo_pod_matrix_row,
)
from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    parse_tabular_workstream_roster,
    reconcile_tabular_workstream_mutations,
)


class TestTabularAndAccountPlanExtractors(unittest.TestCase):
    """Verify cross-page orphan card stitching, POD matrix parsing, template-delta verification, and 0% row suppression."""

    def test_cross_page_orphan_card_stitching_and_90_workstream_portfolio(self) -> None:
        # Build a representative 21-page NEA Team Assignments fixture with 90 workstreams
        # and top-of-page overflow cards on pages 4, 6, 12, 15, 17, and 20.
        overflow_pages = {4, 6, 12, 15, 17, 20}
        pages = []
        ws_counter = 0

        for p_num in range(1, 22):
            ws_on_page = 5 if p_num <= 6 else 4  # 6*5 + 15*4 = 90 workstreams
            ws_blocks = []
            roster_blocks = []

            if p_num in overflow_pages:
                # Add top-of-page overflow cards belonging to previous page's last workstream
                overflow_count = 6 if p_num == 4 else 3
                for ov_i in range(1, overflow_count + 1):
                    roster_blocks.append(f"Overflow Member P{p_num} Card {ov_i}\nContractor")

            for idx in range(ws_on_page):
                ws_counter += 1
                if p_num == 3 and idx == ws_on_page - 1:
                    ws_name = "Conduit Q4: Planning & Engineering (PF3)"
                    pm_name = "Hayden Radovich"
                    sm_name = "Jake Wellington"
                    prod_name = "Conduit - Nuclear, Conduit - Fossil"
                    vert_line = "Gen Asset Opti (Jarod Bryant)"
                    dept_line = "Enterprise AI (Matthew)"
                    nav_line = "In Progress $1,261K 92%"
                else:
                    ws_name = f"Workstream {ws_counter}"
                    pm_name = f"PM Lead {ws_counter}"
                    sm_name = f"SM Lead {ws_counter}"
                    prod_name = f"Platform System {ws_counter}"
                    vert_line = "TechSales (Reid Schenk)"
                    dept_line = "NEA Operations (Chris Nunalee)"
                    nav_line = f"In Progress ${ws_counter * 100}K 95%"

                ws_blocks.append(
                    f"{ws_name}\n"
                    f"Product: {prod_name}\n"
                    f"PM: {pm_name}\n"
                    f"SM: {sm_name}\n"
                    f"{vert_line}\n"
                    f"{dept_line}\n"
                    f"{nav_line}"
                )

                # Right-column roster cards starting with PM anchor
                if p_num == 3 and idx == ws_on_page - 1:
                    # 5 on Page 3 + 6 overflow on Page 4 top = 11 total members
                    roster_blocks.append(f"{pm_name}\nStaff")
                    for m_i in range(1, 5):
                        roster_blocks.append(f"Conduit Engineer {m_i}\nSenior")
                else:
                    roster_blocks.append(f"{pm_name}\nStaff\nSpecialist {ws_counter}\nContractor")
                    if ws_counter % 15 == 0 and not (p_num == 3 and idx == ws_on_page - 1):
                        roster_blocks.append(f"Future Hire #{ws_counter // 15}\nStaff")

            page_md = f"## Page {p_num}\n" + "\n".join(ws_blocks) + "\n" + "\n".join(roster_blocks)
            pages.append(page_md)

        full_doc = "\n\n".join(pages)
        parsed_ws = parse_tabular_workstream_roster(full_doc)
        self.assertEqual(len(parsed_ws), 90)

        # Locate Conduit Q4: Planning & Engineering (PF3) (last workstream on Page 3)
        conduit_pf3 = next(w for w in parsed_ws if "conduit q4: planning and engineering" in w["name"].lower())
        self.assertEqual(len(conduit_pf3["roster"]), 11)
        overflow_names = [m["name"] for m in conduit_pf3["roster"] if "Overflow Member P4" in m["name"]]
        self.assertEqual(len(overflow_names), 6)

        # Reconcile into Output 03
        data = {"evidence_index": {}, "graph_mutations": {}}
        reconcile_tabular_workstream_mutations(data, {"is_document": True}, full_doc)
        ents = {e["id"]: e for e in data["graph_mutations"]["entity_upserts"]}
        sigs = data["graph_mutations"]["relationship_signals"]

        self.assertIn("workstream:conduit-q4-planning-and-engineering-pf3", ents)
        self.assertIn("system:conduit-nuclear-conduit-fossil", ents)
        self.assertIn("metric:nea-q4-portfolio-total-nav-and-assignment", ents)
        self.assertTrue(len(data["graph_mutations"].get("risk_updates", [])) >= 5)

        # Verify all 11 members (including Page 4 overflow) have MEMBER_OF edges to Conduit PF3
        conduit_members = [
            s["from_id"]
            for s in sigs
            if s["to_id"] == "workstream:conduit-q4-planning-and-engineering-pf3" and s["relationship"] == "MEMBER_OF"
        ]
        # 11 roster cards + 1 SM (Jake Wellington) = 12 MEMBER_OF edges
        self.assertGreaterEqual(len(conduit_members), 11)

        # Verify Vertical/Dept leader LEADS edges
        leader_edges = [
            (s["from_id"], s["to_id"])
            for s in sigs
            if s["relationship"] == "LEADS" and s["to_id"].startswith("org:")
        ]
        self.assertIn(("person:jarod-bryant", "org:gen-asset-opti-jarod-bryant"), leader_edges)
        self.assertIn(("person:reid-schenk", "org:techsales-reid-schenk"), leader_edges)

    def test_geo_pod_matrix_parser_and_role_transitions(self) -> None:
        self.assertEqual(expand_abbreviated_person_name("Amit A."), "Amit Ayer")
        self.assertEqual(expand_abbreviated_person_name("Dan O."), "Drew Oakley")

        row_str = (
            "| Geo-POD: WPB, Orl, Jax | Apex Global Enterprises | Tier 1 | $18M | $24M | "
            "Dan O. -> Amit A. | Stephan M. | NEA / EDO | TRM Lakehouse | Red - 3 Open Pod Leads |"
        )
        parsed = parse_geo_pod_matrix_row(row_str)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["account_lead"], "Amit Ayer")
        self.assertEqual(parsed["account_lead_transition"], ("Drew Oakley", "Amit Ayer"))
        self.assertEqual(parsed["delivery_lead"], "Cameron Mercer")

        proj = extract_account_plan_projection(row_str)
        ents = {e["id"]: e for e in proj["graph_mutations"]["entity_upserts"]}
        sigs = proj["graph_mutations"]["relationship_signals"]
        self.assertIn("person:amit-ayer", ents)
        self.assertIn("person:dan-okray", ents)
        transitions = [s for s in sigs if s["relationship"] == "TRANSITIONS_ROLE_TO"]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["from_id"], "person:dan-okray")
        self.assertEqual(transitions[0]["to_id"], "person:amit-ayer")
        self.assertEqual(len(proj["graph_mutations"]["risk_updates"]), 1)

    def test_populated_apex_global_2027_plan_summary_6_zones_and_template_delta(self) -> None:
        dashboard_text = (
            "## Page 1\n"
            "Plan Summary: 2027 Apex Global Enterprises\n"
            "Multi-Year Revenue: $24M ('27) / $38M ('28) / ~$45M ('29)\n"
            "Avg. Bill Rate: $230/$235 | Margin: 48%/50%\n"
            "Business Unit Buyers: NEA $12M, EDO $7M, IT $2M, ISC $1M, PGD $1M, PD $1M\n"
            "Market Profile: Juno FL $22M, St. Paul MN $2M, Richmond VA $6M\n"
            "Priorities: Dominion Merger 2H 2027\n"
            "Big Bets: GridOpti, TRM Lakehouse, Nexus Jarvis\n"
            "Risks: 66/106 SWEs concentration\n"
            "## Page 2\n"
            "Plan Summary by: Capability & GTM Priority Solutions\n"
            "- Cloud & Data Modernization: 45%\n"
            "- AI Office: 0%\n"
            "- AI Factory: 0%\n"
            "- Mainframe Modernization: 0%\n"
        )
        proj = extract_account_plan_projection(dashboard_text)
        ents = {e["id"]: e for e in proj["graph_mutations"]["entity_upserts"]}
        sigs = proj["graph_mutations"]["relationship_signals"]
        audit = proj["template_delta_audit"]

        # Confirmed facts extracted
        self.assertIn("metric:apex_global-multi-year-revenue-2027-2029", ents)
        for bu in ("nea", "edo", "it", "isc", "pgd", "pd"):
            self.assertIn(f"org:apex_global-{bu}", ents)
        for mkt in ("juno-fl", "st-paul-mn", "richmond-va"):
            self.assertIn(f"metric:market-{mkt}", ents)
        self.assertIn("workstream:dominion-merger-2h-2027", ents)
        self.assertIn("workstream:gridopti", ents)
        self.assertIn("workstream:trm-lakehouse", ents)
        self.assertIn("workstream:nexus-jarvis", ents)
        self.assertEqual(len(proj["graph_mutations"]["risk_updates"]), 1)

        # Unmodified template defaults ($230/$235 ABR and 48%/50% Margin) flagged UNVERIFIED_TEMPLATE_DEFAULT
        q_fields = [d["field"] for d in audit["quarantined_template_defaults"]]
        self.assertIn("Avg. Bill Rate ($230/$235)", q_fields)
        self.assertIn("Margin (48%/50%)", q_fields)
        for d in audit["quarantined_template_defaults"]:
            self.assertEqual(d["status"], "UNVERIFIED_TEMPLATE_DEFAULT")

        # 0% rows suppressed from graph mutations
        self.assertIn("AI Office", audit["suppressed_zero_share_rows"])
        self.assertIn("AI Factory", audit["suppressed_zero_share_rows"])
        self.assertIn("Mainframe Modernization", audit["suppressed_zero_share_rows"])
        self.assertNotIn("system:mainframe-modernization", ents)
        self.assertNotIn("system:ai-office", ents)
        self.assertNotIn("system:ai-factory", ents)
        nee_to_mainframe = [
            s for s in sigs if s["from_id"] == "org:apex_global-energy" and "mainframe" in s["to_id"]
        ]
        self.assertEqual(nee_to_mainframe, [])

    def test_real_sample_documents_wave2_portfolio_and_account_plans(self) -> None:
        from pathlib import Path
        from dual_pipeline.pdf_source import inspect_pdf_pages
        from dual_pipeline.pptx_source import extract_pptx_presentation

        home_dir = Path.home()
        repo_root = next(
            (p for p in Path(__file__).resolve().parents if (p / "inputs" / "documents").exists()),
            Path(__file__).resolve().parents[1],
        )

        # 1. Real 21-page NEA Team Assignments PDF
        nea_pdf = repo_root / "inputs" / "documents" / "team_assignments_nea_planning_20261020.pdf"
        if nea_pdf.exists():
            rep_nea = inspect_pdf_pages(nea_pdf.read_bytes(), title=nea_pdf.name)
            ws_list = parse_tabular_workstream_roster(rep_nea.extracted_markdown)
            self.assertEqual(len(ws_list), 90)
            for ws in ws_list:
                names = [m["name"] for m in ws["roster"] if not m["is_fh"] and m["tier"] != "Unfilled Role"]
                self.assertEqual(len(names), len(set(names)), f"Duplicate person cards in workstream {ws['name']}")

        # 2. Real 74-page FL 2027 Account Planning Playbook PDF
        fl_pdf = home_dir / "Desktop" / "Florida 2027 Account Planning Playbook.pdf"
        if fl_pdf.exists():
            rep_fl = inspect_pdf_pages(fl_pdf.read_bytes(), title=fl_pdf.name)
            proj_fl = extract_account_plan_projection(rep_fl.extracted_markdown)
            fl_ents = {e["id"]: e for e in proj_fl["graph_mutations"]["entity_upserts"]}
            self.assertIn("org:apex_global-energy", fl_ents)
            self.assertIn("person:amit-ayer", fl_ents)
            self.assertIn("person:lan-tran", fl_ents)
            self.assertNotIn("org:hertz", fl_ents)
            self.assertNotIn("org:vitas-healthcare", fl_ents)

        # 3. Real 24-slide Populated Apex Global 2027 Account Plan PPTX files
        for p in (
            home_dir / "Downloads" / "Florida 2027 Account Planning NEE.pptx",
            home_dir / "Downloads" / "apex_global_energy_2027_account_plan_updated.pptx",
        ):
            if not p.exists():
                continue
            pres = extract_pptx_presentation(p.read_bytes())
            md = "\n\n".join(s.to_markdown() for s in pres.slides)
            proj = extract_account_plan_projection(md)
            ents = {e["id"]: e for e in proj["graph_mutations"]["entity_upserts"]}
            for corrupt_id in (
                "person:disney",
                "person:universal-destinations",
                "person:red-lobster",
                "org:mike-jarro",
                "org:rich-argentieri",
                "person:champion",
                "system:total",
                "person:market-solutions-leader-ams",
            ):
                self.assertNotIn(corrupt_id, ents, f"Corrupted entity {corrupt_id} leaked in {p.name}")
            for req_id in (
                "org:apex_global-energy",
                "person:amit-ayer",
                "person:rich-argentieri",
                "person:josh-millar",
                "person:chakra-sankaraiah",
                "person:grace-kurian",
                "person:mark-lamasney",
                "person:mike-jarro",
                "person:ben-miron",
                "person:rusty-gattman",
                "person:chad-troline",
                "person:alan-giner",
                "person:justin-stephenson",
            ):
                self.assertIn(req_id, ents, f"Missing required entity {req_id} in {p.name}")


if __name__ == "__main__":
    unittest.main()
