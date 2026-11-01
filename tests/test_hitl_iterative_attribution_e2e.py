"""
tests/test_hitl_iterative_attribution_e2e.py

End-to-end verification for Wave 4:
1. check_speaker_skew_and_collapse detects per-attendee starvation (72 vs 3 turns -> 2.1% < 5%)
   and third-person self-reference contradictions even when max speaker share is 50.7% < 85%.
2. AudioSpeakerAttributionEngine.analyze_and_resolve blocks early-return bypass when
   existing_map conflicts with operator quote hints or exhibits third-person contradictions,
   and iteratively resolves clusters/turns via HITL into hitl_decision_ledger.json.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.collapse_detector import check_speaker_skew_and_collapse


def _build_1021_collapsed_transcript() -> str:
    """Builds a representative 10/21/2026 LENS Debrief transcript (67 Speaker 2, 72 Speaker 1, 3 Speaker 3)."""
    lines = ["## Live Feed\n"]
    for i in range(67):
        lines.append(f"**[00:{i % 60:02d}:00]** **Speaker 2:** Let's review the executive lens framing turn {i}.")
    # Speaker 1 has 72 turns, including Miles's hinted quote and third-person references to both Miles and Skyler
    lines.append(
        "**[00:10:15]** **Speaker 1:** It's just bizarre because like what does the data scientist do they explain numbers."
    )
    lines.append(
        "**[00:11:20]** **Speaker 1:** Why is it calling to all the same things that Skyler may have asked earlier in the session?"
    )
    lines.append(
        "**[00:12:05]** **Speaker 1:** I know Miles is working on the semantic cache tuning for the demo."
    )
    for i in range(69):
        lines.append(f"**[00:20:{i % 60:02d}]** **Speaker 1:** Remote channel dialogue turn {i} on model evaluation.")
    for i in range(3):
        lines.append(f"**[00:34:0{i}]** **Speaker 3:** Short tail utterance {i}.")
    return "\n".join(lines)


def test_collapse_detector_flags_starvation_and_third_person_under_85_percent(tmp_path: Path) -> None:
    """
    Verifies that a 142-turn 3-attendee transcript with 67/72/3 split (max share 50.7% < 85%)
    still halts with HITLRequiredError due to Speaker 3 starvation (2.1% < 5%) and
    third-person contradiction on Speaker 1.
    """
    transcript = _build_1021_collapsed_transcript()
    roster = [
        ("Amit Ayer", "PartnerAdvisory"),
        ("Skyler Carlson", "PartnerAdvisory"),
        ("Miles Erickson", "PartnerAdvisory"),
    ]
    metadata = {
        "project": "project_lens",
        "meeting_name": "Debrief_Skyler_PartnerAdvisory",
        "date": "10/21/2026",
        "speaker_map": {
            "Speaker 1": "Skyler Carlson",
            "Speaker 2": "Amit Ayer",
            "Speaker 3": "Miles Erickson",
        },
        "hitl_ledger_path": str(tmp_path / "hitl_decision_ledger.json"),
    }

    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(transcript, roster, metadata)

    err = exc_info.value
    assert err.hitl_type == "single_speaker_collapse"
    assert "Speaker 3" in err.evidence.get("starved_labels", [])
    contradictions = err.evidence.get("third_person_contradictions", [])
    assert len(contradictions) >= 1
    assert any("Skyler" in c["text"] for c in contradictions)


def test_analyze_and_resolve_blocks_early_return_and_runs_iterative_hitl(tmp_path: Path) -> None:
    """
    Verifies that when existing_map pre-maps Speaker 1..3, analyze_and_resolve does NOT
    short-circuit at `if not unmapped:` when hints and third-person cues conflict with existing_map.
    Instead, it evicts conflicted clusters, runs iterative HITL resolution, and records decisions
    in hitl_decision_ledger.json.
    """
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_path)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    transcript = (
        "## Live Feed\n"
        "**[00:01:00]** **Speaker 2:** Let's walk through the architecture milestones.\n"
        "**[00:02:00]** **Speaker 1:** It's just bizarre because like what does the data scientist do they explain numbers.\n"
        "**[00:03:00]** **Speaker 1:** Why is it calling to all the same things that Skyler may have asked yesterday?\n"
        "**[00:04:00]** **Speaker 3:** From the client side we want the governance gates documented.\n"
        "**[00:05:00]** **Speaker 3:** Let's confirm the delivery timeline for next week.\n"
    )
    metadata = {
        "project": "project_lens",
        "meeting_name": "Debrief_Skyler_PartnerAdvisory",
        "date": "10/21/2026",
        "date_filename": "10-21-2026",
        "roster": {
            "PartnerAdvisory": [
                "Amit Ayer (Architect)",
                "Skyler Carlson (Director)",
                "Miles Erickson (Data Scientist)",
            ]
        },
        # Pre-populated map that wrongly assigns Speaker 1 to Skyler Carlson and Speaker 3 to Miles Erickson
        "speaker_map": {
            "Speaker 1": "Skyler Carlson",
            "Speaker 2": "Amit Ayer",
            "Speaker 3": "Miles Erickson",
        },
        "speaker_hints": [
            {
                "speaker": "Miles Erickson",
                "quote": "what does the data scientist do they explain numbers",
                "bound_speaker_id": "Speaker 1",
            }
        ],
        "hitl_ledger_path": str(ledger_path),
    }

    prompts_seen: list[str] = []

    def _interactive_resolver(question: str, options: list[str]) -> str:
        prompts_seen.append(question)
        if "`Speaker 1`" in question:
            return next(opt for opt in options if "Miles Erickson" in opt)
        if "`Speaker 3`" in question:
            return next(opt for opt in options if "Skyler Carlson" in opt)
        return options[0]

    # Because Speaker 3 was mapped to Miles Erickson, evicting Speaker 1 and resolving Speaker 1 -> Miles Erickson
    # would collide with Speaker 3 unless Speaker 3 is also resolved or updated. Let's include a hint/conflict on Speaker 3 too
    # or let resolver resolve both.
    metadata["speaker_hints"].append({
        "speaker": "Skyler Carlson",
        "quote": "From the client side we want the governance gates documented",
        "bound_speaker_id": "Speaker 3",
    })

    result = engine.analyze_and_resolve(
        transcript,
        metadata,
        interactive_resolver=_interactive_resolver,
        autonomous_if_decisive=False,
    )

    assert len(prompts_seen) >= 2
    assert result.speaker_map["Speaker 1"] == "Miles Erickson"
    assert result.speaker_map["Speaker 3"] == "Skyler Carlson"
    assert "**Miles Erickson:** It's just bizarre" in result.resolved_transcript
    assert "**Miles Erickson:** Why is it calling to all the same things that Skyler may have asked" in result.resolved_transcript
    assert "**Skyler Carlson:** From the client side" in result.resolved_transcript

    # Independent verification of durable ledger entries
    import json
    assert ledger_path.is_file()
    ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
    decisions = [d for d in ledger_data.get("decisions", []) if d.get("category") == "speaker_attribution"]
    assert len(decisions) >= 2
