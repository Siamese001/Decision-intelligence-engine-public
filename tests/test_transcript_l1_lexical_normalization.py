"""Unit tests for L1 forensic lexical and phonetic normalization."""

from __future__ import annotations

import re

from dual_pipeline.transcript_stages import OfflineTranscriptSynthesizer
from dual_pipeline.transcript_stages_core.l1_cognition import (
    L1ForensicCognition,
    L1PlanContract,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import (
    normalize_lexical_and_phonetics,
)


def test_lexical_normalization_phonetic_homophones() -> None:
    raw_text = (
        "**Speaker 1:** We reviewed the SSW with Solom and approved Staffog before the band's over. "
        "The Steer Code met to discuss stair cut options."
    )
    metadata = {
        "meeting_name": "Test Meeting",
        "date": "09/11/2026",
    }
    cleaned = normalize_lexical_and_phonetics(raw_text, metadata)

    assert "SSW" not in cleaned
    assert "SOW" in cleaned
    assert "Solom" not in cleaned
    assert "PartnerAdvisory" in cleaned
    assert "Staffog" not in cleaned
    assert "Staff Aug" in cleaned
    assert "before the band's" not in cleaned
    assert "before the day ends" in cleaned
    assert "Steer Code" not in cleaned
    assert "SteerCo" in cleaned


def test_lexical_normalization_roster_attendees() -> None:
    raw_text = (
        "**Speaker 1:** I caught up with Amit Air and Tony Cablon on the architecture."
    )
    metadata = {
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Tony Kablan"],
        },
        "expected_domain_terms": ["Bonfiglioli", "La Casa"],
    }
    cleaned = normalize_lexical_and_phonetics(raw_text, metadata)

    assert "Amit Air" not in cleaned
    assert "Amit Ayer" in cleaned
    assert "Tony Cablon" not in cleaned
    assert "Tony Kablan" in cleaned


def test_lexical_normalization_domain_terms() -> None:
    raw_text = (
        "**Speaker 1:** We checked the figlioli pitch pitch on the turbine at La Paz."
    )
    metadata = {
        "expected_domain_terms": ["Bonfiglioli", "La Casa"],
    }
    cleaned = normalize_lexical_and_phonetics(raw_text, metadata)

    assert not re.search(r"\bfiglioli\b", cleaned, re.IGNORECASE)
    assert "Bonfiglioli pitch" in cleaned
    assert "La Paz" not in cleaned
    assert "La Casa" in cleaned


def test_lexical_normalization_cutoff_marker() -> None:
    raw_text = (
        "**harper Hoffman:** [26:50] Thanks everyone.\n"
        "**harper Hoffman:** [26:56] Bye bye.\n"
        "**Hallucinated Voice:** [27:30] Some post-call background noise.\n"
    )
    metadata = {
        "cutoff_marker": "**harper Hoffman:** [26:56]",
    }
    cleaned = normalize_lexical_and_phonetics(raw_text, metadata)

    assert "**harper Hoffman:** [26:56]" in cleaned
    assert "Hallucinated Voice" not in cleaned


def test_lexical_normalization_iso_date_sanitization() -> None:
    raw_text = (
        "**Speaker 1:** The event happened on 2026-09-11 during Phase 1."
    )
    metadata = {}
    cleaned = normalize_lexical_and_phonetics(raw_text, metadata)

    assert "2026-09-11" not in cleaned
    assert "09/11/2026" in cleaned


def test_l1_execute_native_applies_normalization() -> None:
    raw_text = (
        "**harper Hoffman:** [03:12] I met with Amit Air to discuss the SSW at La Paz."
    )
    u0_context = {
        "clean_transcript": raw_text,
        "metadata": {
            "meeting_name": "Project LENS Review",
            "date": "2026-09-11",
            "attendees": ["Amit Ayer", "harper Hoffman"],
            "expected_domain_terms": ["La Casa"],
            "speaker_map": {},
        },
    }
    plan = L1PlanContract(
        source_type="DIARIZED_TEXT",
        diarization_status="NATIVE",
        roster_gate_needed=False,
    )
    l1 = L1ForensicCognition()
    provider = OfflineTranscriptSynthesizer()
    output_1 = l1.execute(u0_context, plan, provider)

    assert "**Date:** 09/11/2026" in output_1
    assert "Amit Air" not in output_1
    assert "Amit Ayer" in output_1
    assert "SSW" not in output_1
    assert "SOW" in output_1
    assert "La Paz" not in output_1
    assert "La Casa" in output_1
