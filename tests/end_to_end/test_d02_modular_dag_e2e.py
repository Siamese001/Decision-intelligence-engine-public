"""End-to-end integration test for D02 3-Pass Parallel Modular DAG in LLM synthesis lifecycle."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


def test_d02_modular_dag_synthesizer_lifecycle_e2e():
    """E2E lifecycle: Meeting transcript -> L2ExecutiveRecapExecutor -> 3-Pass DAG -> Post-processed recap."""
    forensic_transcript = (
        "## Forensic Transcript\n\n"
        "[00:00:10] Alex Brennan: Let's align on the Nexus architecture and Google adapter release.\n"
        "[00:01:15] Amit Ayer: Agreed, we must ensure zero regression on Gate 3.4 quote accuracy.\n"
    )


    mock_synth = MagicMock()
    mock_synth.d02_model = "gemini-3.8-flash"

    narrative_pass = (
        "# Executive Recap: Nexus Architecture Alignment\n\n"
        "**Date:** 09/25/2026\n"
        "**Time:** 10:00 AM\n"
        "**Attendees:** Alex Brennan (PartnerAdvisory), Amit Ayer (PartnerAdvisory)\n\n"
        "## Meeting Objective & Executive Summary\n"
        "The team conducted an architecture review of the Gemini 3.8 Flash pipeline migration. "
        + ("Strategic discussion context and technical requirements for the multi-pass DAG execution " * 85)
    )
    ledger_pass = (
        "| Item # | Action / Milestone | Owner | Target Date | Dependencies |\n"
        "| 1 | Complete Wave 5 DAG | Amit Ayer | 09/27/2026 | Unit tests passing |"
    )
    appendix_pass = (
        "# Alex Brennan - PartnerAdvisory, Principal Architect\n"
        "- **Nexus Architecture**: Reviewed provider boundaries and multi-pass DAG execution.\n"
        "# Amit Ayer - PartnerAdvisory, Director of Engineering\n"
        "- **Quality Governance**: Enforced word floor validation and post-processing gates.\n"
        + ("Detailed forensic perspective analysis on stakeholder positions and risk mitigation " * 75)
    )

    with (
        patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            side_effect=[narrative_pass, ledger_pass, appendix_pass],
        ),
        patch.object(
            LLMTranscriptSynthesizer, "__init__", lambda self, *args, **kwargs: None
        ),
    ):
            synth = LLMTranscriptSynthesizer()
            synth.d02_model = "gemini-3.8-flash"
            synth.reasoning_effort = "high"

            recap_output = synth.synthesize_recap(
                system_prompt="Stage 02 prompt",
                user_content=forensic_transcript,
            )

            assert "# Executive Recap: Nexus Architecture Alignment" in recap_output
            assert "## Action & Governance Ledger" in recap_output
            assert "| Item # |" in recap_output
            assert "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown" in recap_output
            assert "Alex Brennan - PartnerAdvisory" in recap_output
            assert "Amit Ayer - PartnerAdvisory" in recap_output
