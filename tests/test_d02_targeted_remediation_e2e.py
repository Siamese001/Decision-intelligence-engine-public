"""End-to-end integration test for Deliverable 02 Section-Targeted Remediation.

Verifies:
1. A remediation attempt that fails only on the 2C appendix issues exactly 1 pass call (not 3).
2. Frozen sections (2A narrative and 2B ledger) remain byte-for-byte and SHA256 hash-identical.
3. A remediation attempt targeting only 2B issues exactly 1 pass call with 2A and 2C frozen.
4. A remediation attempt targeting only 2A issues exactly 1 pass call with 2B and 2C frozen.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger
from dual_pipeline.transcript_stages_core.recap_coherence_gate import (
    compute_section_hashes,
    parse_recap_sections,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import execute_d02_3pass_dag


class D02TargetedRemediationE2ETests(unittest.TestCase):
    """E2E suite verifying section-targeted remediation and byte-identical freezing."""

    def setUp(self) -> None:
        self.narrative_section = (
            "# Executive Recap: Architecture Alignment\n\n"
            "**Date:** 10/03/2026\n"
            "**Time:** 10:00 AM\n"
            "**Attendees:** Alex Brennan (PartnerAdvisory), Amit Ayer (PartnerAdvisory)\n\n"
            "## Meeting Objective & Executive Summary\n"
            "The engineering leadership reviewed the dual pipeline architecture and token optimizations. "
            + ("Detailed technical discussion on system boundaries, release lifecycle, and caching invariants. " * 50)
        ).strip()
        self.ledger_section = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Deploy Caching Layer | Amit Ayer | 10/05/2026 | Unit tests green |\n"
            "| 2 | Review Gate Receipts | Alex Brennan | 10/06/2026 | Verification passing |"
        ).strip()
        self.perspectives_section = (
            "# Alex Brennan - PartnerAdvisory, Principal Architect\n"
            "- **Architecture**: Outlined modular DAG boundaries and token economy.\n\n"
            "# Amit Ayer - PartnerAdvisory, Director of Engineering\n"
            "- **Governance**: Mandated section-targeted remediation and single retry owner policy.\n"
            + ("Forensic perspective detail on technical positions and risk considerations. " * 50)
        ).strip()
        self.initial_recap = (
            f"{self.narrative_section}\n\n"
            f"## Action & Governance Ledger\n\n"
            f"{self.ledger_section}\n\n"
            f"# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            f"{self.perspectives_section}"
        )
        self.initial_hashes = compute_section_hashes(parse_recap_sections(self.initial_recap))

    def test_targeted_remediation_2c_only_issues_1_call_and_freezes_2a_2b(self) -> None:
        """Remediation targeting only Appendix issues 1 call (2C); 2A & 2B remain byte- and hash-identical."""
        updated_perspectives = (
            "# Alex Brennan - PartnerAdvisory, Principal Architect\n"
            "- **Architecture & DAG**: Expanded forensic analysis of ThreadPoolExecutor and timeout limits.\n\n"
            "# Amit Ayer - PartnerAdvisory, Director of Engineering\n"
            "- **Governance & Quality**: Comprehensive audit of Rule C-21 contributions with deep nuance.\n"
            + ("Deep forensic expanded stakeholder detail covering all perspectives thoroughly. " * 60)
        ).strip()

        call_records = []

        def fake_call(model, system_prompt, user_content, **kwargs):
            pass_id = kwargs.get("pass_id", "unknown")
            call_records.append({"pass_id": pass_id, "kwargs": kwargs})
            ledger_inst = kwargs.get("ledger")
            if ledger_inst:
                ledger_inst.record_call(
                    pass_id=pass_id,
                    pass_attempt=kwargs.get("pass_attempt", 1),
                    transport_attempt=1,
                    model=model,
                    status="SUCCESS",
                    finish_reason="STOP",
                    usage_metadata={"promptTokenCount": 100, "candidatesTokenCount": 150, "totalTokenCount": 250},
                    latency_ms=50.0,
                )
            return updated_perspectives

        ledger = D02CallLedger(operation_id="d02-remed-2c")
        critique = "The Appendix: Detailed Stakeholder Perspectives lacks sufficient depth for Amit Ayer (Rule C-21)."

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=fake_call):
            revised_recap = execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="archetype system prompt",
                user_content="<forensic_transcript>Alex Brennan and Amit Ayer discussed DAG.</forensic_transcript>",
                min_words_2a=100,
                min_words_2c=80,
                ledger=ledger,
                prior_candidate=self.initial_recap,
                remediation_directive=critique,
                enable_caching=False,
            )

        # 1. Verify call counts: exactly 1 call was issued!
        self.assertEqual(len(call_records), 1)
        self.assertEqual(call_records[0]["pass_id"], "2C")

        summary = ledger.compute_budget_summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["calls_by_pass"], {"2C": 1})

        # 2. Verify frozen sections are byte-for-byte and hash-identical
        revised_sections = parse_recap_sections(revised_recap)
        revised_hashes = compute_section_hashes(revised_sections)

        # 2A and 2B are frozen byte-identically
        self.assertEqual(revised_sections["2A"], self.narrative_section)
        self.assertEqual(revised_hashes["2A"], self.initial_hashes["2A"])
        self.assertEqual(revised_sections["2B"], self.ledger_section)
        self.assertEqual(revised_hashes["2B"], self.initial_hashes["2B"])

        # 2C was updated with the new content
        self.assertNotEqual(revised_hashes["2C"], self.initial_hashes["2C"])
        self.assertIn("Expanded forensic analysis", revised_sections["2C"])

    def test_targeted_remediation_2b_only_issues_1_call_and_freezes_2a_2c(self) -> None:
        """Remediation targeting only Action Ledger issues 1 call (2B); 2A & 2C remain hash-identical."""
        updated_ledger = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Deploy Caching Layer | Amit Ayer | 10/05/2026 | Unit tests green |\n"
            "| 2 | Review Gate Receipts | Alex Brennan | 10/06/2026 | Verification passing |\n"
            "| 3 | Finalize SOW Boundaries | Alex Brennan | 10/08/2026 | Legal review complete |"
        )

        call_records = []

        def fake_call(model, system_prompt, user_content, **kwargs):
            pass_id = kwargs.get("pass_id", "unknown")
            call_records.append({"pass_id": pass_id})
            ledger_inst = kwargs.get("ledger")
            if ledger_inst:
                ledger_inst.record_call(
                    pass_id=pass_id,
                    pass_attempt=kwargs.get("pass_attempt", 1),
                    transport_attempt=1,
                    model=model,
                    status="SUCCESS",
                    finish_reason="STOP",
                    usage_metadata={"promptTokenCount": 100, "candidatesTokenCount": 150, "totalTokenCount": 250},
                    latency_ms=50.0,
                )
            return updated_ledger

        ledger = D02CallLedger(operation_id="d02-remed-2b")
        critique = "The Action Ledger table format is missing item 3 agreed during the discussion."

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=fake_call):
            revised_recap = execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="archetype system prompt",
                user_content="<forensic_transcript>Alex Brennan and Amit Ayer discussed SOW.</forensic_transcript>",
                min_words_2a=100,
                min_words_2c=80,
                ledger=ledger,
                prior_candidate=self.initial_recap,
                remediation_directive=critique,
                enable_caching=False,
            )

        # 1 call made, exclusively for 2B
        self.assertEqual(len(call_records), 1)
        self.assertEqual(call_records[0]["pass_id"], "2B")

        summary = ledger.compute_budget_summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["calls_by_pass"], {"2B": 1})

        # 2A and 2C remain hash-identical
        revised_sections = parse_recap_sections(revised_recap)
        revised_hashes = compute_section_hashes(revised_sections)
        self.assertEqual(revised_hashes["2A"], self.initial_hashes["2A"])
        self.assertEqual(revised_hashes["2C"], self.initial_hashes["2C"])
        self.assertEqual(revised_sections["2B"], updated_ledger)

    def test_targeted_remediation_2a_only_issues_1_call_and_freezes_2b_2c(self) -> None:
        """Remediation targeting only Narrative issues 1 call (2A); 2B & 2C remain hash-identical."""
        updated_narrative = (
            "# Executive Recap: Architecture Alignment\n\n"
            "**Date:** 10/03/2026\n"
            "**Time:** 10:00 AM\n"
            "**Attendees:** Alex Brennan (PartnerAdvisory), Amit Ayer (PartnerAdvisory)\n\n"
            "## Meeting Objective & Executive Summary\n"
            "The engineering leadership resolved critical token economy and retry discipline challenges. "
            + ("Extensive revised narrative with additional strategic context and key findings. " * 50)
        ).strip()

        call_records = []

        def fake_call(model, system_prompt, user_content, **kwargs):
            pass_id = kwargs.get("pass_id", "unknown")
            call_records.append({"pass_id": pass_id})
            ledger_inst = kwargs.get("ledger")
            if ledger_inst:
                ledger_inst.record_call(
                    pass_id=pass_id,
                    pass_attempt=kwargs.get("pass_attempt", 1),
                    transport_attempt=1,
                    model=model,
                    status="SUCCESS",
                    finish_reason="STOP",
                    usage_metadata={"promptTokenCount": 100, "candidatesTokenCount": 150, "totalTokenCount": 250},
                    latency_ms=50.0,
                )
            return updated_narrative

        ledger = D02CallLedger(operation_id="d02-remed-2a")
        critique = "The Executive Summary in the strategic narrative is too brief and needs expansion."

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=fake_call):
            revised_recap = execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="archetype system prompt",
                user_content="<forensic_transcript>Alex Brennan and Amit Ayer were present.</forensic_transcript>",
                min_words_2a=100,
                min_words_2c=80,
                ledger=ledger,
                prior_candidate=self.initial_recap,
                remediation_directive=critique,
                enable_caching=False,
            )

        # 1 call made, exclusively for 2A
        self.assertEqual(len(call_records), 1)
        self.assertEqual(call_records[0]["pass_id"], "2A")

        summary = ledger.compute_budget_summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["calls_by_pass"], {"2A": 1})

        # 2B and 2C remain hash-identical
        revised_sections = parse_recap_sections(revised_recap)
        revised_hashes = compute_section_hashes(revised_sections)
        self.assertEqual(revised_hashes["2B"], self.initial_hashes["2B"])
        self.assertEqual(revised_hashes["2C"], self.initial_hashes["2C"])
        self.assertEqual(revised_sections["2A"], updated_narrative)


if __name__ == "__main__":
    unittest.main()
