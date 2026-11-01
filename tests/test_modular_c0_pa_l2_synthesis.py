"""
tests/test_modular_c0_pa_l2_synthesis.py

Tests for Stage C0 context resolution, PA packet assembly, and L2 synthesis rectification.
"""
import tempfile

import pytest

from dual_pipeline.auto_scaffolder import PreflightAutoScaffolder
from dual_pipeline.transcript_pipeline_core.context_builder import (
    assemble_pa_packet,
    initialize_remediation_state_machine,
    resolve_c0_context_and_attribution,
)
from dual_pipeline.transcript_stages import (
    C0ConformanceGate,
    C0RosterContextGate,
    L1PlanContract,
    OfflineTranscriptSynthesizer,
    PAPacketAssembler,
)


def test_resolve_c0_native_path():
    """Verify native path clears conformance gate without requiring roster."""
    c0_native = C0ConformanceGate()
    c0_undiarized = C0RosterContextGate()
    auto_scaffolder = PreflightAutoScaffolder(OfflineTranscriptSynthesizer())

    plan = L1PlanContract(
        source_type="DIARIZED_TEXT",
        diarization_status="NATIVE",
        roster_gate_needed=False,
    )
    u0_ctx = {
        "clean_transcript": "**Alice (PartnerAdvisory):** Hello world.",
        "hash": "test_hash_123",
        "metadata": {
            "meeting_name": "Sync",
            "date": "09/19/2026",
            "time": "10:00",
            "classification": "internal",
        },
    }
    l0_ctx = {
        "transcript": u0_ctx["clean_transcript"],
        "metadata": u0_ctx["metadata"],
        "statements": [],
    }

    c0_ctx, resolved_text, is_pre_resolved = resolve_c0_context_and_attribution(
        plan=plan,
        u0_context=u0_ctx,
        l0_context=l0_ctx,
        transcript_text=u0_ctx["clean_transcript"],
        input_path=None,
        is_notes_mode=False,
        c0_native=c0_native,
        c0_undiarized=c0_undiarized,
        auto_scaffolder=auto_scaffolder,
    )

    assert c0_ctx.get("conformance_checked") is True
    assert c0_ctx.get("roster_gate_cleared") is True
    assert resolved_text == u0_ctx["clean_transcript"]
    assert is_pre_resolved is False


def test_resolve_c0_undiarized_scaffolder_fallback():
    """Verify undiarized path triggers auto-scaffolding when roster is absent."""
    c0_native = C0ConformanceGate()
    c0_undiarized = C0RosterContextGate()
    auto_scaffolder = PreflightAutoScaffolder(OfflineTranscriptSynthesizer())
    auto_scaffolder.execute = lambda t: {"attendee_roster": ["Alice (PartnerAdvisory)", "Bob (Apex Global Enterprises)"]}

    plan = L1PlanContract(
        source_type="UNDIARIZED_TEXT",
        diarization_status="UNDIARIZED",
        roster_gate_needed=True,
    )
    transcript = "Alice from PartnerAdvisory discussed cloud migration with Bob from Apex Global Enterprises."
    u0_ctx = {
        "clean_transcript": transcript,
        "hash": "test_hash_456",
        "metadata": {
            "meeting_name": "Migration Planning",
            "date": "09/19/2026",
            "time": "11:00",
            "classification": "internal",
        },
    }
    l0_ctx = {
        "transcript": transcript,
        "metadata": u0_ctx["metadata"],
        "statements": [],
    }

    c0_ctx, resolved_text, is_pre_resolved = resolve_c0_context_and_attribution(
        plan=plan,
        u0_context=u0_ctx,
        l0_context=l0_ctx,
        transcript_text=transcript,
        input_path=None,
        is_notes_mode=False,
        c0_native=c0_native,
        c0_undiarized=c0_undiarized,
        auto_scaffolder=auto_scaffolder,
    )

    assert c0_ctx.get("roster_gate_cleared") is True
    assert "ingestion_context" in u0_ctx["metadata"]


def test_initialize_remediation_state_machine_pre_resolved():
    """Verify pre-resolved transcript freezes output_1 in state machine."""
    pre_resolved_text = (
        "# Sync -- HITL-Resolved Transcript\n\n"
        "**Date:** 09/19/2026\n"
        "**Platform Reference:** Teams\n"
        "**Participants:**\n- **PartnerAdvisory:** Alice\n\n---\n"
        "**Alice (PartnerAdvisory):** We are ready to launch.\n"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        sm = initialize_remediation_state_machine(
            metadata={"meeting_name": "Sync"},
            output_dir=tmpdir,
            max_judge_retries=1,
            is_pre_resolved_input=True,
            is_notes_mode=False,
            transcript_text=pre_resolved_text,
        )
        assert sm.is_frozen("output_1")
        assert sm.get_frozen_content("output_1") == pre_resolved_text


def test_assemble_pa_packet():
    """Verify Stage PA packet assembly completes with valid context."""
    pa = PAPacketAssembler()
    metadata = {
        "meeting_name": "Architecture Review",
        "date": "09/19/2026",
        "classification": "internal",
    }
    l0_context = {
        "transcript": "Architecture decisions made.",
        "statements": [],
        "metadata": metadata,
    }

    pa_context = assemble_pa_packet(pa, l0_context, metadata)
    assert pa_context is not None
    assert "target_outputs" in pa_context or "metadata" in pa_context


def test_offline_synthesizer_respects_forbidden_sections_for_internal_sync():
    """Verify offline synthesizer omits forbidden sections for internal technical sync archetype."""
    from dual_pipeline.transcript_stages_core.offline_synthesizer import (
        OfflineTranscriptSynthesizer,
    )

    synthesizer = OfflineTranscriptSynthesizer()
    prompt = "Synthesize executive recap."
    user_content = (
        "Context: Internal sync between architects.\n"
        "Archetype: INTERNAL_TECHNICAL_SYNC\n"
        "<raw_transcript>\n**Amit Ayer (PartnerAdvisory):** We validated the pipeline.\n</raw_transcript>"
    )

    recap = synthesizer.synthesize_recap(prompt, user_content)
    # Ensure forbidden sections are not emitted for internal sync
    assert "## Immediate Delivery Scope & Release Boundaries" not in recap
    assert "## Business Meaning and Value Case" not in recap
    assert "# Subject:" in recap
    assert "## Meeting Objective" in recap


def test_c0_roster_enrichment_and_placeholder_hitl():
    """Verify C0 roster gate auto-enriches known roles and flags uncalibrated placeholders."""
    from dual_pipeline.transcript_stages_core.c0_gates import C0RosterContextGate
    from dual_pipeline.transcript_stages_core.models import HITLRequiredError

    gate = C0RosterContextGate()

    # 1. Auto-enrichment: Jake Stark with generic role gets enriched from directory
    l0_ctx = {
        "metadata": {
            "roster": [
                {"name": "Jake Stark", "role": "PartnerAdvisory Team Contributor", "org": "PartnerAdvisory"},
                {"name": "Miles Erickson", "role": "", "org": "PartnerAdvisory"},
            ],
        }
    }
    res = gate.execute(l0_ctx)
    roster_out = res["metadata"]["roster"]
    assert roster_out[0]["role"] == "UI and Prompting Lead"
    assert roster_out[1]["role"] == "Software Engineer / Technical Contributor"

    # 2. Unknown person with placeholder role raises HITLRequiredError
    l0_ctx_unknown = {
        "metadata": {
            "roster": [
                {"name": "Unknown Person", "role": "Participant", "org": "UnknownOrg"},
            ],
        }
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_ctx_unknown)
    assert exc_info.value.hitl_type == "role_clarification"
