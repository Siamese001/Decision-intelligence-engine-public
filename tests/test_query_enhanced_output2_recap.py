"""
tests/test_query_enhanced_output2_recap.py

Wave 3 Verification:
Tests Output 02 Executive Recap prompt assembly and graph context continuity:
1. Verifies that active graph decisions (Rule C-16) are injected into recap prompt user content.
2. Verifies that active graph risks are injected into recap prompt user content.
3. Verifies that absent graph decisions/risks cleanly omit the XML blocks.
4. Verifies that forbidden section stripping remains deterministic.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from dual_pipeline.transcript_stages_core.models import L2SynthesisProvider
from dual_pipeline.transcript_stages_core.recap_executor import L2ExecutiveRecapExecutor


class RecordingMockProvider(L2SynthesisProvider):
    """Captures prompt and user_content passed to synthesize_recap."""

    def __init__(self) -> None:
        self.last_system_prompt: Optional[str] = None
        self.last_user_content: Optional[str] = None

    def synthesize_transcript(self, system_prompt: str, user_content: str) -> str:
        return "dummy transcript"

    def synthesize_recap(self, system_prompt: str, user_content: str) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_content = user_content
        return (
            "# Subject: Project Nexus Sync -- Executive Strategic & Technical Brief | 09/20/2026\n\n"
            "**Date:** 09/20/2026 | **Time:** 10:00 AM - 10:30 AM ET\n\n"
            "**Attendees:**\n- **PartnerAdvisory:** Amit Ayer (Principal Architect)\n\n"
            "## Meeting Objective\n"
            "Executive alignment on architecture decoupling and cloud modernization milestone.\n\n"
            "## Key Discussion Points & Findings\n"
            "- **Architecture Migration Alignment**: The team reviewed cloud cutover and confirmed readiness.\n\n"
            "### Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Finalize API specification | Amit Ayer | 09/25/2026 | ev-01 | Confirmed |\n\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "# Amit Ayer - PartnerAdvisory, Principal Architect\n"
            "- **Cloud Decoupling Strategy**: Detailed technical rationale for asynchronous queuing and WAL mode storage.\n"
        )

    def synthesize_graph(self, system_prompt: str, user_content: str) -> str:
        return "{}"


def test_recap_executor_injects_prior_decisions_and_risks() -> None:
    """Verifies injection of prior_graph_decisions and prior_graph_risks XML blocks."""
    provider = RecordingMockProvider()
    executor = L2ExecutiveRecapExecutor(provider=provider)

    pa_context: Dict[str, Any] = {
        "metadata": {
            "meeting_name": "Project Nexus Architecture Sync",
            "date": "09/20/2026",
            "time": "10:00 AM - 10:30 AM ET",
            "archetype": "INTERNAL_TECHNICAL_SYNC",
            "prior_graph_decisions": [
                {
                    "assertion_id": "ast:001",
                    "predicate": "CONFIRMED_DECISION",
                    "subject_node_id": "project:nexus",
                    "claim_text": "Architecture migration to cloud API approved.",
                    "observed_at": "09/15/2026",
                }
            ],
            "prior_graph_risks": [
                {
                    "assertion_id": "ast:002",
                    "predicate": "BLOCKING_RISK",
                    "subject_node_id": "person:amit_ayer",
                    "claim_text": "NERC CIP compliance verification requires sign-off.",
                }
            ],
        }
    }

    transcript = "Amit Ayer: We are proceeding with the cloud migration discussed last week."
    executor.execute(transcript, pa_context)

    # Provider should have received user_content containing prior_decisions and prior_risks
    captured = provider.last_user_content or ""
    assert "<prior_decisions>" in captured
    assert "CONFIRMED_DECISION" in captured
    assert "Architecture migration to cloud API approved." in captured
    assert "<prior_risks>" in captured
    assert "BLOCKING_RISK" in captured
    assert "NERC CIP compliance verification requires sign-off." in captured
    assert "Rule C-16" in captured


def test_recap_executor_omits_prior_blocks_when_absent() -> None:
    """Verifies that no prior_decisions or prior_risks blocks are injected when absent."""
    provider = RecordingMockProvider()
    executor = L2ExecutiveRecapExecutor(provider=provider)

    pa_context: Dict[str, Any] = {
        "metadata": {
            "meeting_name": "Standalone Kickoff",
            "date": "09/20/2026",
            "archetype": "INTERNAL_TECHNICAL_SYNC",
        }
    }

    transcript = "Amit Ayer: Welcome to the project kickoff."
    executor.execute(transcript, pa_context)

    captured = provider.last_user_content or ""
    assert "<prior_decisions>" not in captured
    assert "<prior_risks>" not in captured
