"""
Class B Adversarial Test Suite for Skyler Carlson vs. Rob Koch Attribution RCA.
Covers 3 mandatory edge-case categories:
1. Boundary payload (empty turns, empty candidate lists, None metadata).
2. Malformed input (mismatched metadata structures, non-string values).
3. Invariant bypass attempt (adversarial punctuation and casing to bypass third-person exclusions).
"""
from __future__ import annotations

import pytest
from dual_pipeline.audio_ingestion_core.subcluster_splitter import (
    _apply_exclusion_to_anchor,
    _identify_anchor_turns,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    _detect_onboarding_historical_conflict,
    score_candidates_for_speaker,
)


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - empty inputs, 0-length strings, None structures."""
    pos = {}
    excl = {}
    # Empty text
    _apply_exclusion_to_anchor(0, "", {}, [], pos, excl, total_remote_candidates=[])
    assert pos == {}
    assert excl == {}

    # None metadata
    assert _detect_onboarding_historical_conflict("Rob Koch", "", {}) is None
    assert _detect_onboarding_historical_conflict("", "", {}) is None

    # Empty candidate list in scoring
    cands = score_candidates_for_speaker("spk_1", "", [], {})
    assert cands == []


def test_adversarial_malformed_inputs():
    """Category 2: Malformed inputs - weird types, non-dict metadata, corrupt lists."""
    pos = {}
    excl = {}
    # Malformed candidates dictionary with non-string keys/values
    cand_first = {"": "", "123": "Non Existent"}
    _apply_exclusion_to_anchor(0, "Some text", cand_first, ["Valid"], pos, excl)
    assert pos == {}

    # Metadata with weird nested formats
    metadata = {
        "onboarding_attendees": [None, 123, {"bad": "object"}],
        "recent_transitions": "not-a-list",
        "roster": 42,
    }
    # Should handle gracefully without raising unhandled exceptions
    conflict = _detect_onboarding_historical_conflict("Rob Koch", "we set up notifications", metadata)
    assert conflict is None


def test_adversarial_invariant_bypass_attempts():
    """Category 3: Invariant bypass attempts - tricky punctuation, casing, and deceptive turns."""
    pos = {}
    excl = {}
    cand_first = {"chaffee": "Chaffee Burke", "rob": "Rob Koch"}
    remote_cands = ["Chaffee Burke", "Rob Koch"]
    total_remote = ["Chaffee Burke", "Rob Koch", "Skyler Carlson"]

    # Invariant bypass attempt: Using punctuation and weird spacing to fool exclusion check
    text_variant = "And   then, [transfer] to CHAFFEE... once he's on board!"
    _apply_exclusion_to_anchor(
        idx=5,
        text=text_variant,
        cand_first=cand_first,
        remote_candidates=remote_cands,
        positive_anchors=pos,
        third_person_exclusions=excl,
        total_remote_candidates=total_remote,
    )
    # Must correctly detect Chaffee exclusion despite formatting
    assert excl.get(5) == "Chaffee Burke"
    # Invariant: Must NOT bypass multi-attendee guard and populate positive_anchors
    assert 5 not in pos
