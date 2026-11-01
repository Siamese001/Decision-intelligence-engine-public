"""
Adversarial and edge-case verification for Wave 1:
Covers >= 4 mandatory edge-case categories:
1. Boundary payload: Empty utterances, empty quotes, 1-word short stopwords vs 1-word distinctive hints.
2. Malformed input: Corrupt/non-dict hint items, missing speaker/quote keys, curly apostrophes (`’`).
3. Invariant bypass attempt: Multi-speaker conflict on a single collapsed cluster (`SPEAKER_01` has hints for both
   Miles Erickson and Skyler Carlson) — ensures whole-cluster propagation is blocked while individual turns are preserved.
4. Exact production regression: `10/21/2026` `SPEAKER_01` vs `SPEAKER_00` appearance order with `assert_injective_speaker_mapping`
   when `Speaker 1` and `SPEAKER_01` map to the same person while `SPEAKER_00` maps to `Amit Ayer`.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
    SpeakerAttributionCollisionError,
)
from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
    apply_quote_hints_to_turns,
    apply_quote_hints_to_utterances,
)


def test_boundary_and_malformed_hint_payloads() -> None:
    """Category 1 (Boundary) & Category 2 (Malformed): Empty inputs, non-dict hints, curly apostrophes, substring false-positive guard."""
    assert apply_quote_hints_to_turns("", []) == ("", {})
    assert apply_quote_hints_to_utterances([], []) == ([], {})

    malformed_hints: List[Any] = [
        None,
        "invalid_string",
        {"speaker_name": "", "quote": "something"},
        {"speaker_name": "Miles Erickson", "quote": ""},
        {"speaker_name": "Miles Erickson", "quote": "he’s old"},  # Unicode right single quotation mark
    ]
    transcript = (
        "**Speaker 1** [00:00:10]: I think he's old enough to remember that system.\n"
        "**Speaker 2** [00:00:20]: Implicitly we agreed.\n"
    )
    updated, reassignments = apply_quote_hints_to_turns(transcript, malformed_hints)  # type: ignore[arg-type]
    assert "**Miles Erickson** [00:00:10]:" in updated
    assert len(reassignments) == 1

    # Single-word hint "art" must NOT substring-match inside "start" or "part"
    single_word_hint = [{"speaker_name": "Miles Erickson", "quote": "art"}]
    t2 = "**Speaker 1** [00:01:00]: Wait until we start changing part of the model.\n"
    updated_t2, reass_t2 = apply_quote_hints_to_turns(t2, single_word_hint)
    assert reass_t2 == {}
    assert "**Speaker 1**" in updated_t2


def test_conflicting_hints_on_same_cluster_do_not_clobber_entire_cluster() -> None:
    """Category 3 (Invariant Bypass): If a collapsed cluster has hints for two different speakers, turn-level hints apply without clobbering the whole cluster."""
    utterances = [
        {
            "utterance_id": "u1",
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "text": "The point that I'm making, even implicitly, why is it calling to all the same things?",
        },
        {
            "utterance_id": "u2",
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "text": "I reviewed the random forest accuracy levers with the team yesterday.",
        },
        {
            "utterance_id": "u3",
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "text": "Unmatched turn on the same collapsed remote cluster.",
        },
    ]
    hints = [
        {"speaker_name": "Miles Erickson", "quote": "implicitly"},
        {"speaker_name": "Skyler Carlson", "quote": "random forest accuracy levers"},
    ]
    updated_utts, cluster_to_speaker = apply_quote_hints_to_utterances(utterances, hints)
    # Because SPEAKER_01 has hints for TWO distinct speakers, cluster_to_speaker must NOT assign all of SPEAKER_01 to one person
    assert "SPEAKER_01" not in cluster_to_speaker
    assert updated_utts[0]["speaker"] == "Miles Erickson"
    assert updated_utts[1]["speaker"] == "Skyler Carlson"
    assert updated_utts[2]["speaker"] == "SPEAKER_01"
    # raw_speaker is preserved for sub-clustering / HITL
    assert updated_utts[0]["raw_speaker"] == "SPEAKER_01"
    assert updated_utts[1]["raw_speaker"] == "SPEAKER_01"


def test_production_regression_injective_mapping_with_appearance_order_cluster_map() -> None:
    """Category 4 (Exact Production Regression): SPEAKER_01 == Speaker 1 and SPEAKER_00 == Speaker 2 must pass injective check when consistent, and fail when genuinely duplicated."""
    cluster_label_map = {
        "SPEAKER_01": "Speaker 1",
        "SPEAKER_00": "Speaker 2",
        "SPEAKER_02": "Speaker 3",
    }
    valid_speaker_map = {
        "SPEAKER_01": "Miles Erickson",
        "Speaker 1": "Miles Erickson",
        "SPEAKER_00": "Amit Ayer",
        "Speaker 2": "Amit Ayer",
        "SPEAKER_02": "Skyler Carlson",
        "Speaker 3": "Skyler Carlson",
    }
    # Without cluster_label_map, SPEAKER_01 would normalize to Speaker 2 and collide with Amit Ayer!
    # With cluster_label_map, SPEAKER_01 normalizes to Speaker 1 (Miles Erickson) and passes cleanly.
    AudioSpeakerAttributionEngine.assert_injective_speaker_mapping(
        valid_speaker_map,
        roster_count=3,
        cluster_count=3,
        cluster_label_map=cluster_label_map,
    )

    # Genuine duplicate assignment across two distinct clusters still fails closed
    duplicate_speaker_map = {
        "Speaker 1": "Skyler Carlson",
        "Speaker 2": "Amit Ayer",
        "Speaker 3": "Skyler Carlson",
    }
    with pytest.raises(SpeakerAttributionCollisionError):
        AudioSpeakerAttributionEngine.assert_injective_speaker_mapping(
            duplicate_speaker_map,
            roster_count=3,
            cluster_count=3,
            cluster_label_map=cluster_label_map,
        )
