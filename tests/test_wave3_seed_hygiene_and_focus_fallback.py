"""Wave 3 tests: Contraction-Safe Seed Hygiene & Active-Session Focus Window Fallback."""
from __future__ import annotations

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
    extract_turn_entities,
)


def test_contraction_stems_and_conversational_fillers_excluded_from_seeds() -> None:
    """Turns containing contractions ('didn't', 'doesn't', 'won't') and fillers ('Sometimes', 'Like', 'Yeah', 'feel') must not pollute retrieval_seeds."""
    cm = ConversationContextModel(project_scope="project:partner_alliances", attendees=["Amit", "Stefanos"])
    cm.add_turn(
        speaker="Stefanos",
        text="Sometimes I feel like we didn't get the Early Access Program numbers, and it doesn't seem like KPMG is able to join.",
        timestamp="00:01:00",
        turn_sec=60.0,
    )
    cm.add_turn(
        speaker="Amit",
        text="Yeah, Didn't we say we're moving the CoE charter forward?",
        timestamp="00:01:15",
        turn_sec=75.0,
    )

    seeds = cm.retrieval_seeds()
    seeds_low = {s.lower() for s in seeds}
    for forbidden in ("didn", "doesn", "sometimes", "feel", "able", "like", "yeah", "does", "we're"):
        assert forbidden not in seeds_low, f"Forbidden token {forbidden!r} leaked into retrieval_seeds: {seeds}"
    assert any("Early Access Program" in s or "KPMG" in s or "CoE" in s for s in seeds)

    # Also test lowercase-only fallback turn where no proper nouns exist
    low_ents = extract_turn_entities("sometimes i feel like they didn't and doesn't know if we are able to work on kubernetes")
    low_set = {e.lower() for e in low_ents}
    assert "didn" not in low_set
    assert "doesn" not in low_set
    assert "feel" not in low_set
    assert "able" not in low_set
    assert "kubernetes" in low_set


def test_focus_turns_fallback_preserves_multi_turn_context_after_long_pause() -> None:
    """When an explicit 45s focus window sees a >120s pause or quarantined turn, focus_turns falls back to recent non-quarantined turns."""
    cm = ConversationContextModel(
        project_scope="project:partner_alliances",
        attendees=["Amit", "Stefanos"],
        focus_seconds=45.0,
    )
    cm.add_turn("Stefanos", "First point on the Gemini Enterprise rollout.", "00:00:10", turn_sec=10.0)
    cm.add_turn("Amit", "Second point on the fifty user pilot cohort.", "00:00:25", turn_sec=25.0)
    # Quarantined prompt-echo turn at 300s must not advance _latest_time and age out valid turns
    cm.add_turn(
        "Speaker",
        "Meeting context: Google NEE PartnerAdvisory Monthly. Attendees: Amit, Stefanos. Technical and domain vocabulary: Apex Global Enterprises.",
        "00:05:00",
        turn_sec=300.0,
    )
    focus = cm.focus_turns()
    assert len(focus) == 2
    assert focus[0]["speaker"] == "Stefanos"
    assert focus[1]["speaker"] == "Amit"
