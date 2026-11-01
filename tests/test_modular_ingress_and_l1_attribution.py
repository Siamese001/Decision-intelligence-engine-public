"""
tests/test_modular_ingress_and_l1_attribution.py

Unit tests for Wave 2 modularized Stage U0 Ingress and Stage L1 Attribution.
Verifies:
1. Stage U0 text normalization, hashing, and idempotency bypass via ingress.py.
2. Stage L1 speaker map application, generic speaker detection, and transcript assembly via l1_attribution.py.
3. Budget invariant: l1_cognition.py is strictly under 400 lines.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.transcript_pipeline_core.ingress import (
    U0IngressResult,
    process_u0_ingress,
)
from dual_pipeline.transcript_stages_core.l1_attribution import (
    apply_speaker_map,
    assemble_native_hitl_transcript,
    check_generic_speakers,
)
from dual_pipeline.transcript_stages_core.l1_cognition import (
    HITLRequiredError,
    L1ForensicCognition,
    L1PlanContract,
)
from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer


def test_l1_cognition_module_budget() -> None:
    """Verify that l1_cognition.py is strictly under 400 lines."""
    cognition_path = Path(__file__).resolve().parent.parent / "dual_pipeline" / "transcript_stages_core" / "l1_cognition.py"
    line_count = sum(1 for _ in open(cognition_path, "r", encoding="utf-8"))
    assert line_count < 400, f"l1_cognition.py has {line_count} lines (budget limit: 400)"


def test_u0_ingress_inline_text(tmp_path: Path) -> None:
    """Verify process_u0_ingress with inline text input."""
    raw = "**Speaker A** [00:00:00]: Hello team.\n**Speaker B** [00:00:05]: Good morning."
    res = process_u0_ingress(
        input_path=raw,
        metadata={"meeting_name": "Sprint Sync", "date": "09/19/2026"},
    )
    assert isinstance(res, U0IngressResult)
    assert len(res.input_hash) == 64
    assert not res.is_file
    assert "Hello team." in res.transcript_text
    assert "source_modality" in res.metadata


def test_u0_ingress_file_input(tmp_path: Path) -> None:
    """Verify process_u0_ingress with file on disk."""
    p = tmp_path / "meeting.txt"
    p.write_text("**Amit Ayer:** Welcome everyone.\n**Cameron Mercer:** Thanks Amit.", encoding="utf-8")

    res = process_u0_ingress(
        input_path=str(p),
        metadata={"meeting_name": "Architecture Review"},
    )
    assert res.is_file is True
    assert res.source_file_hash is not None
    assert len(res.source_file_hash) == 64
    assert "Welcome everyone." in res.transcript_text


def test_l1_attribution_mapping() -> None:
    """Verify apply_speaker_map replaces generic tokens accurately."""
    raw = "**Speaker A**: Starting review.\nSpeaker B: Agreed.\nSpeaker A continues."
    mapping = {
        "Speaker A": "Amit Ayer (PartnerAdvisory)",
        "Speaker B": "Cameron Mercer (PartnerAdvisory)",
    }
    resolved = apply_speaker_map(raw, mapping)
    assert "**Amit Ayer (PartnerAdvisory):** Starting review." in resolved
    assert "Cameron Mercer (PartnerAdvisory):** Agreed." in resolved or "Cameron Mercer (PartnerAdvisory): Agreed." in resolved
    assert "Amit Ayer (PartnerAdvisory) continues." in resolved


def test_l1_attribution_generic_speaker_block() -> None:
    """Verify check_generic_speakers raises HITLRequiredError when unmapped, and bypass raises ValueError."""
    raw = "**Speaker A** [00:00:00]: Hello.\n**Speaker X** [00:00:05]: Unknown speaker."
    mapping = {"Speaker A": "Amit Ayer"}

    with pytest.raises(HITLRequiredError, match=r"(?i)unmapped generic speakers"):
        check_generic_speakers(raw, mapping, source_type="AUDIO_ONLY_UNDIARIZED")

    # Bypass attempts must fail closed with ValueError
    with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
        check_generic_speakers(raw, mapping, bypass_hitl=True)

    # Calling check_generic_speakers with unmapped speakers raises HITLRequiredError
    with pytest.raises(HITLRequiredError, match=r"(?i)unmapped generic speakers"):
        check_generic_speakers(raw, mapping)

    # When all generic speakers are mapped, returns empty list
    full_mapping = {"Speaker A": "Amit Ayer", "Speaker X": "Alex Smith", "Unknown speaker": "Sam Doe"}
    unmapped = check_generic_speakers(raw, full_mapping)
    assert unmapped == []


def test_l1_attribution_assemble_hitl_transcript() -> None:
    """Verify assemble_native_hitl_transcript produces full Output 01 markdown."""
    resolved_dialogue = "**Amit Ayer:** We are aligned.\n**Reid Schenk:** Approved."
    metadata = {
        "meeting_name": "Governance Alignment",
        "date": "09/19/2026",
        "platform": "Microsoft Teams",
        "attendees": ["Amit Ayer", "Reid Schenk"],
    }
    out = assemble_native_hitl_transcript(resolved_dialogue, metadata, {})
    assert "# Governance Alignment -- HITL-Resolved Transcript" in out
    assert "**Date:** 09/19/2026" in out
    assert "## Ambiguity Lock Register" in out
    assert "| Item # | Identified Ambiguity |" in out


def test_l1_cognition_execution_with_attribution() -> None:
    """Verify end-to-end L1ForensicCognition.execute() uses modular attribution."""
    l1 = L1ForensicCognition()
    provider = OfflineTranscriptSynthesizer()
    u0_ctx = {
        "clean_transcript": "**Speaker A** [00:00:00]: Action ratified.\n**Speaker B** [00:00:10]: Confirmed.",
        "metadata": {
            "meeting_name": "Executive Ratification",
            "date": "09/19/2026",
            "speaker_map": {
                "Speaker A": "Amit Ayer (PartnerAdvisory)",
                "Speaker B": "Cameron Mercer (PartnerAdvisory)",
            },
        },
        "diarization_status": "NATIVE",
    }
    plan = L1PlanContract(
        source_type="DIARIZED_TEXT",
        diarization_status="NATIVE",
        roster_gate_needed=False,
    )
    result = l1.execute(u0_ctx, plan, provider)
    assert "Amit Ayer (PartnerAdvisory)" in result
    assert "Cameron Mercer (PartnerAdvisory)" in result
    assert "## Ambiguity Lock Register" in result
