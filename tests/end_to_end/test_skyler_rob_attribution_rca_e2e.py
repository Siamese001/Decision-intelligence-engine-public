"""
Class B E2E Test Suite for Skyler Carlson vs. Rob Koch Attribution RCA.
Verifies multi-speaker remote candidate resolution and tenure-aware attribution.
"""
from __future__ import annotations

import pytest
from dual_pipeline.audio_ingestion_core.subcluster_splitter import (
    _apply_exclusion_to_anchor,
    _identify_anchor_turns,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    score_candidates_for_speaker,
    _detect_onboarding_historical_conflict,
)


def test_anchor_inversion_guarded_in_multi_remote_meeting():
    """Third-person mention in a 7-person meeting does not invert to the only other candidate."""
    cand_first = {"chaffee": "Chaffee Burke", "rob": "Rob Koch"}
    remote_candidates = ["Chaffee Burke", "Rob Koch"]
    total_remote = [
        "Rob Koch",
        "Chaffee Burke",
        "Miles Erickson",
        "Skyler Carlson",
        "Jake Stark",
        "Morgan Vance Krutilla",
    ]
    positive_anchors = {}
    third_person_exclusions = {}

    text = "And then transfer to Chaffee once he's on board."
    _apply_exclusion_to_anchor(
        idx=0,
        text=text,
        cand_first=cand_first,
        remote_candidates=remote_candidates,
        positive_anchors=positive_anchors,
        third_person_exclusions=third_person_exclusions,
        total_remote_candidates=total_remote,
    )

    # Chaffee is excluded from speaking this turn
    assert third_person_exclusions[0] == "Chaffee Burke"
    # But positive_anchors must NOT blindly default to Rob Koch because 4 other candidates exist!
    assert 0 not in positive_anchors


def test_anchor_inversion_allowed_in_strict_two_speaker_room():
    """In a strictly 2-candidate room, third-person mention can invert to the only other candidate."""
    cand_first = {"chaffee": "Chaffee Burke", "rob": "Rob Koch"}
    remote_candidates = ["Chaffee Burke", "Rob Koch"]
    total_remote = ["Chaffee Burke", "Rob Koch"]
    positive_anchors = {}
    third_person_exclusions = {}

    text = "Chaffee, can you take the lead on this?"
    _apply_exclusion_to_anchor(
        idx=0,
        text=text,
        cand_first=cand_first,
        remote_candidates=remote_candidates,
        positive_anchors=positive_anchors,
        third_person_exclusions=third_person_exclusions,
        total_remote_candidates=total_remote,
    )

    assert third_person_exclusions[0] == "Chaffee Burke"
    assert positive_anchors[0] == "Rob Koch"


def test_onboarding_tenure_conflict_detection():
    """Historical setup phrases trigger tenure conflict for onboarding members."""
    metadata = {
        "onboarding_attendees": ["Rob Koch", "Chaffee Burke"],
    }
    history_text = "yeah so we set up notifications for when items are in review or rejected"
    conflict = _detect_onboarding_historical_conflict("Rob Koch", history_text, metadata)
    assert conflict is not None

    # Skyler Carlson is not onboarding -> no conflict
    assert _detect_onboarding_historical_conflict("Skyler Carlson", history_text, metadata) is None


def test_e2e_skyler_vs_rob_attribution_resolution():
    """In a simulated debrief, historical notification setup attributes to Skyler rather than Rob."""
    transcript = """
### [00:29:10]
**Voice D:** Yeah, so we set up notifications for when items are in review or rejected, but I was never able to get them.

### [00:29:25]
**Voice D:** The thing is there, it sends a daily email, basically notifying of any, saying like, go look at your items.
"""
    roster = [
        ("Rob Koch", "PartnerAdvisory"),
        ("Skyler Carlson", "PartnerAdvisory"),
    ]
    metadata = {
        "onboarding_attendees": ["Rob Koch"],
        "roster": [
            {"name": "Rob Koch", "role": "Tech Lead", "is_onboarding": True},
            {"name": "Skyler Carlson", "role": "Lead - NEA Charter & Conduit: PF3 - Planning and Scoping"},
        ],
    }

    cands = score_candidates_for_speaker(
        spk_id="Voice D",
        transcript=transcript,
        roster=roster,
        metadata=metadata,
        total_clusters=2,
    )

    top_cand = cands[0]
    assert top_cand.attendee_name == "Skyler Carlson"
    assert top_cand.is_recommended is True
    # Rob Koch should have the historical conflict evidence point
    rob_cand = next(c for c in cands if c.attendee_name == "Rob Koch")
    assert "Historical project tenure conflict" in rob_cand.evidence_rationale
