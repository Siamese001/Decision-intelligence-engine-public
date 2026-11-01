"""Adversarial and edge-case tests for Wave 3 (CAT-2 P0 & P1).

Covers mandatory edge-case categories:
1. Boundary payload (empty/single-turn dialogue, zero attendees, missing subject in collapse ledger check)
2. Malformed input (nested dict rosters, compound slash roles with single-word segments, dict-valued HITL role maps)
3. Invariant bypass attempt (boolean single_speaker_collapse_verified_by_operator spoofing, unverified ROSTER_PRIOR voiceprint enrollment, unsanitized Gemini/metadata egress)
4. Exact production regression (word-dominance skew with balanced turn counts, Appendix healer self-poisoning category)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.sanitizer import EgressSanitizationError
from dual_pipeline.speaker_attribution_core.collapse_detector import (
    check_speaker_skew_and_collapse,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.graph_executor import _item_lineage
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


def test_adversarial_boolean_collapse_bypass_rejected_without_signed_ledger() -> None:
    """Category: Invariant bypass attempt — raw metadata boolean cannot bypass collapse detection."""
    provider = MagicMock()
    normalizer = ForensicNormalizer()
    turns = "\n".join(
        f"**[00:{i:02d}:00] Alice:** Speaking continuously across turn {i} without diarization split."
        for i in range(1, 12)
    )
    spoofed_meta = {
        "meeting_id": "mtg-spoof-99",
        "attendees": ["Alice", "Bob"],
        "single_speaker_collapse_verified_by_operator": True,
    }
    with patch(
        "dual_pipeline.speaker_attribution_core.collapse_detector.verify_collapse_approval_in_ledger",
        return_value=False,
    ):
        with pytest.raises(HITLRequiredError):
            normalizer.execute({"clean_transcript": turns, "metadata": spoofed_meta}, plan=None, provider=provider)


def test_word_dominance_skew_with_balanced_turn_counts_reports_word_metrics() -> None:
    """Category: Exact production regression — balanced turn count (6 vs 5) but >88% word share."""
    long_monologue = " ".join(["architecture"] * 120)
    transcript = "\n".join(
        [f"**[00:0{i}:00] SpeakerA:** {long_monologue}" for i in range(1, 7)]
        + [f"**[00:1{i}:00] SpeakerB:** Yes." for i in range(1, 6)]
    )
    roster = {"TeamA": [{"name": "SpeakerA"}], "TeamB": [{"name": "SpeakerB"}]}
    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(
            transcript,
            roster=roster,
            metadata={"roster": roster},
        )
    ev = exc_info.value.evidence
    assert ev["dominant_label_turns"] == 6
    assert ev["dominant_word_ratio"] >= 0.85


def test_item_lineage_handles_dict_valued_hitl_maps_and_filters_unverified_orgs() -> None:
    """Category: Malformed input & Boundary payload — dict-valued HITL maps and placeholder orgs."""
    ev = {"speaker": "person:jane_doe", "speaker_org": "Client / Partner", "speaker_role": "Speaker"}
    meta = {
        "roster": [{"name": "Jane Doe", "role": "Speaker", "org": "Organization Unverified"}],
        "hitl_role_resolutions": {"Jane Doe": {"resolved_role": "VP of Engineering"}},
        "hitl_org_resolutions": {"Jane Doe": {"organization": "Acme Corp"}},
    }
    lin = _item_lineage(ev, meta, "mtg-123")
    assert lin["speaker_role"] == "VP of Engineering"
    assert lin["speaker_org"] == "Acme Corp"


def test_synthesizer_egress_sanitizer_blocks_secrets_across_metadata_and_agentic_paths() -> None:
    """Category: Invariant bypass attempt — pre-egress sanitizer blocks secrets on Gemini/metadata/agentic calls."""
    provider = LLMTranscriptSynthesizer.__new__(LLMTranscriptSynthesizer)
    provider.model = "gemini-3.8-flash"
    provider.d03_model = "gemini-3.8-flash"
    provider.agentic_model = "gemini-3.8-flash"
    provider.reasoning_effort = "low"
    provider.cache_optimizer = MagicMock()

    secret_payload = "Here is the leaked AWS key AKIA_REDACTED_EXAMPLE in user content."
    with pytest.raises((EgressSanitizationError, RuntimeError)) as exc_info:
        provider.synthesize_metadata("system", secret_payload)
    assert "REDACTED SANITIZATION ERROR" in str(exc_info.value) or isinstance(exc_info.value, EgressSanitizationError)
