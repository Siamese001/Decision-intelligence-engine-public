"""
Adversarial and edge-case tests for Wave 1:
- Category 1 (Malformed input): 1-pipe string without Markdown table separator, header-only table with 0 data rows.
- Category 2 (Invariant bypass attempt): Pass 2B table with 'Unassigned (Pending Owner Assignment)', 'Team', 'Speaker A', or lazy 'TBD' timing; Stage 01 turns with '**Unknown:**' or '**Unattributed:**'; native single-speaker collapse attempting autonomous LLM re-attribution.
- Category 3 (Exact production regression): Cross-speaker 1-2 word micro-turn absorption in ForensicNormalizer._stitch_acoustic_micro_turns.
- Category 4 (Overflow & exhaustion): Pass 2B retry budget exhaustion raising Pass2BStructureError and setting abort_event.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.l1_attribution import _find_generic_speakers, check_generic_speakers
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    Pass2BStructureError,
    execute_pass_2b,
    validate_pass_2b_structure,
)


def test_malformed_single_pipe_and_empty_tables_rejected() -> None:
    """Malformed input: single pipe or header-only markdown table must raise Pass2BStructureError."""
    with pytest.raises(Pass2BStructureError):
        validate_pass_2b_structure("Item | Something without a markdown table separator")

    header_only = (
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    )
    with pytest.raises(Pass2BStructureError):
        validate_pass_2b_structure(header_only)


@pytest.mark.parametrize(
    "bad_owner,bad_timing",
    [
        ("Unassigned (Pending Owner Assignment)", "10/25/2026"),
        ("Unassigned", "10/25/2026"),
        ("Team", "10/25/2026"),
        ("All", "10/25/2026"),
        ("Speaker A", "10/25/2026"),
        ("Alex Rivera", "TBD"),
        ("Alex Rivera", "TBD - Unspecified"),
    ],
)
def test_invariant_bypass_pass_2b_rejects_unassigned_owners_and_tbd_dates(bad_owner: str, bad_timing: str) -> None:
    """Invariant bypass attempt: Pass 2B must reject unowned filler rows and lazy TBD dates."""
    table = (
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        f"| 1 | Action | Deliver updated architecture diagram | {bad_owner} | {bad_timing} | [00:12:00] | Open |"
    )
    with pytest.raises(Pass2BStructureError):
        validate_pass_2b_structure(table)


def test_overflow_exhaustion_pass_2b_sets_abort_event_after_max_retries() -> None:
    """Overflow & exhaustion: exhausting max_retries raises Pass2BStructureError and sets sibling abort_event."""
    abort_ev = threading.Event()
    with patch(
        "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
        return_value="broken | non-table output",
    ):
        with pytest.raises(Pass2BStructureError, match="failed after 2 retries"):
            execute_pass_2b(
                model="gemini-3.8-flash",
                user_content="test",
                abort_event=abort_ev,
                max_retries=2,
            )
    assert abort_ev.is_set() is True


def test_regression_cross_speaker_orphan_micro_turns_never_absorbed() -> None:
    """Exact production regression: 1-2 word lowercase turn by a different speaker must not be absorbed into previous speaker."""
    dialogue = (
        "### [00:01:00]\n\n"
        "**Carlos Gomez:** We completed the sensor ingestion pipeline yesterday.\n\n"
        "### [00:01:05]\n\n"
        "**Amit Ayer:** yeah.\n\n"
        "### [00:01:08]\n\n"
        "**harper Hoffman:** that works."
    )
    stitched = ForensicNormalizer._stitch_acoustic_micro_turns(dialogue)
    assert "**Amit Ayer:** yeah." in stitched
    assert "**harper Hoffman:** that works." in stitched
    assert "yesterday. yeah." not in stitched


def test_invariant_bypass_unknown_and_unattributed_speakers_caught() -> None:
    """Invariant bypass attempt: **Unknown:** and **Unattributed:** dialogue turns must be flagged even when native turns exist."""
    mixed_transcript = (
        "### [00:01:00]\n\n"
        "**Amit Ayer:** Welcome everyone.\n\n"
        "### [00:01:15]\n\n"
        "**Unknown:** I have a question on the budget.\n\n"
        "### [00:01:30]\n\n"
        "**Unattributed:** We will review that next."
    )
    detected = _find_generic_speakers(mixed_transcript)
    assert "Unknown" in detected
    assert "Unattributed" in detected

    with pytest.raises(HITLRequiredError):
        check_generic_speakers(
            mixed_transcript,
            speaker_map={},
            metadata={"roster": ["Amit Ayer", "Carlos Gomez"]},
        )


def test_invariant_bypass_single_speaker_collapse_halts_instead_of_llm_guessing() -> None:
    """Invariant bypass attempt: multi-attendee native transcript collapsed to 1 speaker must raise HITLRequiredError."""
    collapsed_transcript = (
        "### [00:01:00]\n\n**Amit Ayer:** Turn one.\n\n"
        "### [00:02:00]\n\n**Amit Ayer:** Turn two.\n\n"
        "### [00:03:00]\n\n**Amit Ayer:** Turn three.\n\n"
        "### [00:04:00]\n\n**Amit Ayer:** Turn four."
    )
    u0_context = {
        "clean_transcript": collapsed_transcript,
        "metadata": {
            "date": "10/15/2026",
            "roster": ["Amit Ayer", "Carlos Gomez", "harper Hoffman"],
            "diarization_status": "NATIVE",
        },
    }
    normalizer = ForensicNormalizer()
    mock_provider = MagicMock()
    with pytest.raises(HITLRequiredError, match="Single-speaker collapse detected"):
        normalizer.execute(u0_context, SimpleNamespace(diarization_status="NATIVE"), mock_provider)
    mock_provider.synthesize_forensic.assert_not_called()
