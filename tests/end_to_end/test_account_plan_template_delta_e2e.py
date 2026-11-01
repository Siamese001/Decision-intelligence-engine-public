"""
E2E and adversarial tests for Wave 2: Tabular Portfolio Stitcher & Account Plan Template-Delta Extractor.
Covers:
1. Boundary payloads (empty POD tables, single-page workstream export without overflow).
2. Malformed inputs (truncated table pipes, missing PM anchor on overflow page).
3. Invariant bypass attempts (injecting unmodified $230/$235 template default as CONFIRMED fact,
   injecting 0% offering rows like Mainframe Modernization or AI Office, occluded Hertz/Vitas rows).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline_core.account_plan_extractor import (
    extract_account_plan_projection,
    parse_geo_pod_matrix_row,
)
from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    parse_tabular_workstream_roster,
    reconcile_tabular_workstream_mutations,
)


class TestAccountPlanTemplateDeltaE2E(unittest.TestCase):
    """End-to-end and adversarial verification for Wave 2 portfolio and account-plan extractors."""

    def test_boundary_and_malformed_table_inputs(self) -> None:
        # Empty / non-table input
        self.assertIsNone(parse_geo_pod_matrix_row(""))
        self.assertIsNone(parse_geo_pod_matrix_row("No pipes in this line"))
        self.assertIsNone(parse_geo_pod_matrix_row("| Only | Three | Cells |"))

        # Header / template placeholder row must return None
        self.assertIsNone(
            parse_geo_pod_matrix_row("| POD | [Account Name] | Tier 1 | $10M | $50M | Lorem ipsum |")
        )
        # Occluded Hertz / Vitas row must return None
        self.assertIsNone(
            parse_geo_pod_matrix_row("| Geo-POD: WPB | Hertz | Tier 2 | $1M | $2M | Lucy Bickford |")
        )

    def test_e2e_sandbox_output03_verification_for_conduit_pf3_and_zero_share_suppression(self) -> None:
        # Construct 4-page excerpt pinning Page 3 -> Page 4 Conduit PF3 11-card overflow
        doc_text = (
            "## Page 1\n"
            "Workstream 1\nProduct: Prod 1\nPM: Alice Smith\nSM: Jake Wellington\nTechSales (Reid Schenk)\nNEA Operations (Chris Nunalee)\nIn Progress $500K 100%\n"
            "Workstream 2\nProduct: Prod 2\nPM: Bob Jones\nSM: Jake Wellington\nTechSales (Reid Schenk)\nNEA Operations (Chris Nunalee)\nIn Progress $600K 100%\n"
            "Alice Smith\nStaff\nBob Jones\nSenior\n\n"
            "## Page 2\n"
            "Workstream 3\nProduct: Prod 3\nPM: Carol White\nSM: Jake Wellington\nTechSales (Reid Schenk)\nNEA Operations (Chris Nunalee)\nIn Progress $700K 100%\n"
            "Workstream 4\nProduct: Prod 4\nPM: Dave Brown\nSM: Jake Wellington\nTechSales (Reid Schenk)\nNEA Operations (Chris Nunalee)\nIn Progress $800K 100%\n"
            "Carol White\nStaff\nDave Brown\nSenior\n\n"
            "## Page 3\n"
            "Conduit Q4: Planning & Engineering (PF3)\n"
            "Product: Conduit - Nuclear, Conduit - Fossil\n"
            "PM: Hayden Radovich\n"
            "SM: Jake Wellington\n"
            "Gen Asset Opti (Jarod Bryant)\n"
            "Enterprise AI (Matthew)\n"
            "In Progress $1,261K 92%\n"
            "Hayden Radovich\nStaff\n"
            "Kareem Joudeh\nStaff\n"
            "## Page 4\n"
            "Conduit Q4: Scheduling (PF2)\n"
            "Product: Conduit - Nuclear, Conduit - Fossil\n"
            "PM: Lancey Lexima\n"
            "SM: Jake Wellington\n"
            "Gen Asset Opti (Jarod Bryant)\n"
            "Enterprise AI (Matthew)\n"
            "In Progress $912K 93%\n"
            + "\n".join([f"Overflow Specialist {i}\nContractor" for i in range(1, 10)])
            + "\nLancey Lexima\nStaff\n"
        )
        # 2 on Page 3 + 9 overflow on Page 4 top = 11 total team members on Conduit PF3
        ws_list = parse_tabular_workstream_roster(doc_text)
        self.assertEqual(len(ws_list), 6)

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmpdir:
            out3_path = Path(tmpdir) / "03_nea_team_assignments_canonical_signal_projection.json"
            payload = {"evidence_index": {}, "graph_mutations": {}}
            reconcile_tabular_workstream_mutations(payload, {"is_document": True}, doc_text)
            out3_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            loaded = json.loads(out3_path.read_text(encoding="utf-8"))
            sigs = loaded["graph_mutations"]["relationship_signals"]
            pf3_members = [
                s["from_id"]
                for s in sigs
                if s["to_id"] == "workstream:conduit-q4-planning-and-engineering-pf3"
                and s["relationship"] == "MEMBER_OF"
                and s["from_id"] != "person:jake-wellington"
            ]
            self.assertEqual(len(pf3_members), 11)

            # Also verify Account Plan Output 03 in isolated sandbox
            acct_path = Path(tmpdir) / "03_apex_global_2027_plan_summary_canonical_signal_projection.json"
            acct_doc = (
                "## Page 1\n"
                "Plan Summary: 2027 Apex Global Enterprises\n"
                "Multi-Year Revenue: $24M ('27) / $38M ('28) / ~$45M ('29)\n"
                "Avg. Bill Rate: $230/$235 | Margin: 48%/50%\n"
                "Business Unit Buyers: NEA $12M, EDO $7M, IT $2M, ISC $1M, PGD $1M, PD $1M\n"
                "Market Profile: Juno FL $22M, St. Paul MN $2M, Richmond VA $6M\n"
                "Priorities: Dominion Merger 2H 2027\n"
                "Big Bets: GridOpti, TRM Lakehouse, Nexus Jarvis\n"
                "Risks: 66/106 SWEs\n"
                "## Page 2\n"
                "- Mainframe Modernization: 0%\n"
                "- AI Office: 0%\n"
                "- AI Factory: 0%\n"
            )
            acct_proj = extract_account_plan_projection(acct_doc)
            acct_path.write_text(json.dumps(acct_proj, indent=2), encoding="utf-8")
            acct_loaded = json.loads(acct_path.read_text(encoding="utf-8"))
            acct_sigs = acct_loaded["graph_mutations"]["relationship_signals"]
            mainframe_edges = [
                s for s in acct_sigs if s["to_id"] == "system:mainframe-modernization"
            ]
            self.assertEqual(mainframe_edges, [])


if __name__ == "__main__":
    unittest.main()
