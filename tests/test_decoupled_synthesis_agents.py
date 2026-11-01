"""
tests/test_decoupled_synthesis_agents.py

Verification suite for decoupled intensive synthesis agents:
- dual_pipeline.transcript_stages_core.forensic_normalizer.ForensicNormalizer
- dual_pipeline.transcript_stages_core.recap_executor.L2ExecutiveRecapExecutor
- dual_pipeline.transcript_stages_core.graph_executor.L2GraphExtractorExecutor
- dual_pipeline.transcript_stages_core.l1_cognition.L1ForensicCognition
"""
from __future__ import annotations

import json

from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.graph_executor import L2GraphExtractorExecutor
from dual_pipeline.transcript_stages_core.l1_cognition import L1ForensicCognition, L1PlanContract
from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
from dual_pipeline.transcript_stages_core.recap_executor import L2ExecutiveRecapExecutor


def test_forensic_normalizer_native():
    normalizer = ForensicNormalizer()
    u0_ctx = {
        "clean_transcript": "Speaker 1 [00:00:01]: Good morning team.\nSpeaker 2 [00:00:10]: Hello everyone.",
        "metadata": {
            "speaker_map": {"Speaker 1": "Alice (PartnerAdvisory)", "Speaker 2": "Bob (Client)"},
            "meeting_name": "Standup",
            "date": "09/12/2026",
        },
    }
    plan = L1PlanContract(
        source_type="DIARIZED_TEXT",
        diarization_status="NATIVE",
        roster_gate_needed=False,
    )
    provider = OfflineTranscriptSynthesizer()
    result = normalizer.execute(u0_ctx, plan, provider)
    assert "Alice (PartnerAdvisory)" in result
    assert "Bob (Client)" in result
    assert "## Ambiguity Lock Register" in result


def test_forensic_normalizer_undiarized():
    normalizer = ForensicNormalizer()
    u0_ctx = {
        "clean_transcript": "Good morning team. We have consensus on the BGE-M3 model release.",
        "metadata": {
            "meeting_name": "Nexus Technical Sync",
            "date": "09/12/2026",
            "roster": ["Alice", "Bob"],
        },
    }
    plan = L1PlanContract(
        source_type="UNDIARIZED_TEXT",
        diarization_status="UNDIARIZED",
        roster_gate_needed=True,
    )
    provider = OfflineTranscriptSynthesizer()
    result = normalizer.execute(u0_ctx, plan, provider)
    assert "## Ambiguity Lock Register" in result
    assert len(result) > 50


def test_recap_executor_synthesis():
    provider = OfflineTranscriptSynthesizer()
    recap_agent = L2ExecutiveRecapExecutor(provider)
    transcript = (
        "Alice [00:00:01]: Welcome everyone. We will evaluate our current sprint progress.\n"
        "Bob [00:00:15]: The integration tests passed across all environments.\n"
        "Alice [00:00:30]: Excellent, let us approve the release candidate."
    )
    pa_context = {
        "metadata": {
            "meeting_name": "Sprint Review",
            "archetype": "INTERNAL_TECHNICAL_SYNC",
            "date": "09/12/2026",
            "time": "10:00 AM",
            "attendees": ["Alice", "Bob"],
        }
    }
    recap_output = recap_agent.execute(transcript, pa_context)
    assert "## Meeting Objective" in recap_output or "Sprint Review" in recap_output
    assert len(recap_output) > 100


def test_graph_executor_synthesis():
    provider = OfflineTranscriptSynthesizer()
    graph_agent = L2GraphExtractorExecutor(provider)
    transcript = "Alice [00:00:01]: We decided to deploy BGE-M3."
    pa_context = {
        "metadata": {
            "meeting_name": "Architecture Review",
            "date": "09/12/2026",
        }
    }
    graph_output = graph_agent.execute(transcript, pa_context=pa_context)
    data = json.loads(graph_output)
    assert "nodes" in data or "statements" in data or "graph_mutations" in data


def test_l1_cognition_planner_pure_and_delegation():
    l1 = L1ForensicCognition()
    u0_ctx = {
        "clean_transcript": "Alice: Hi.\nBob: Bye.",
        "metadata": {
            "meeting_name": "Quick Sync",
            "date": "09/12/2026",
        },
    }
    # Plan pass should be pure and fast
    plan = l1.plan(u0_ctx)
    assert isinstance(plan, L1PlanContract)
    assert plan.sanitization_passed is True

    # Execute pass should delegate cleanly to ForensicNormalizer
    provider = OfflineTranscriptSynthesizer()
    res = l1.execute(u0_ctx, plan, provider)
    assert len(res) > 20
