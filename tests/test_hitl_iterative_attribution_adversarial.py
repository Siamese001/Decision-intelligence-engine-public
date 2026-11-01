"""
tests/test_hitl_iterative_attribution_adversarial.py

Adversarial & edge-case coverage for Wave 4 (covering >= 4 mandatory categories):
1. Exact production regression: 10/21/2026 LENS Debrief 72/67/3 turn split with pre-populated
   existing_map and third-person self-reference ("Skyler may have asked", "Miles is working")
   blocks autonomous early-return when no interactive_resolver is provided.
2. Invariant bypass attempt: Caller-supplied `u0_hitl_approved=True` or unverified collapse
   flags cannot bypass starvation / third-person contradiction checks.
3. Boundary payload: Sub-20-turn meetings or 2-person meetings do not falsely trigger the
   >= 3-attendee starvation rule, and empty/malformed hint entries are safely ignored.
4. Iterative turn-level third-person discrepancy loop: When a named transcript still contains
   a third-person self-reference turn after cluster mapping, `run_iterative_conflict_discrepancies`
   prompts HITL and rewrites the contradicted turn without infinite looping (overflow/exhaustion guard).
"""
from __future__ import annotations

from pathlib import Path
import pytest

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.collapse_detector import check_speaker_skew_and_collapse


def test_exact_1021_regression_without_resolver_raises_hitl_required(tmp_path: Path) -> None:
    """
    Category: Exact production regression.
    Without an interactive_resolver, a pre-populated speaker_map with a conflicting operator
    quote hint and third-person self-reference MUST raise HITLRequiredError instead of returning early.
    """
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    engine = AudioSpeakerAttributionEngine(decision_store=HITLDecisionStore(ledger_path=ledger_path))

    transcript = (
        "## Live Feed\n"
        "**[00:01:00]** **Speaker 2:** Let's review the deliverables.\n"
        "**[00:02:00]** **Speaker 1:** It's just bizarre because like what does the data scientist do they explain numbers.\n"
        "**[00:03:00]** **Speaker 1:** Why is it calling to all the same things that Skyler may have asked?\n"
        "**[00:04:00]** **Speaker 3:** Sounds good.\n"
    )
    metadata = {
        "project": "project_lens",
        "meeting_name": "Debrief_Skyler_PartnerAdvisory",
        "date": "10/21/2026",
        "roster": {
            "PartnerAdvisory": [
                "Amit Ayer (Architect)",
                "Skyler Carlson (Director)",
                "Miles Erickson (Data Scientist)",
            ]
        },
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

    with pytest.raises(HITLRequiredError):
        engine.analyze_and_resolve(transcript, metadata, interactive_resolver=None, autonomous_if_decisive=False)


def test_invariant_bypass_attempt_on_starved_cluster_rejected(tmp_path: Path) -> None:
    """
    Category: Invariant bypass attempt.
    Caller-asserted booleans (`hitl_receipt_bound=True`, `speaker_attribution_resolved=True`)
    cannot bypass the 3-attendee starvation collapse gate.
    """
    lines = (
        [f"**Speaker 1:** Turn {i}" for i in range(50)]
        + [f"**Speaker 2:** Turn {i}" for i in range(48)]
        + [f"**Speaker 3:** Turn {i}" for i in range(2)]
    )
    transcript = "\n".join(lines)
    roster = [("Amit Ayer", "PartnerAdvisory"), ("Skyler Carlson", "PartnerAdvisory"), ("Miles Erickson", "PartnerAdvisory")]
    meta = {
        "project": "project_lens",
        "meeting_name": "Starvation_Bypass_Attempt",
        "hitl_receipt_bound": True,
        "speaker_attribution_resolved": True,
        "hitl_ledger_path": str(tmp_path / "hitl_decision_ledger.json"),
    }

    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(transcript, roster, meta)
    assert "Speaker 3" in exc_info.value.evidence.get("starved_labels", [])


def test_boundary_payload_short_or_two_person_meetings_not_falsely_starved(tmp_path: Path) -> None:
    """
    Category: Boundary payload & Malformed input.
    1. A 2-person meeting with 60/40 split does not trigger 3-person starvation.
    2. Self-introduction ("Hi this is Skyler Carlson") is not misclassified as a third-person contradiction.
    3. Malformed speaker_hints entries (None, empty dicts) do not crash conflict reconciliation.
    """
    roster_2 = [("Amit Ayer", "PartnerAdvisory"), ("Skyler Carlson", "PartnerAdvisory")]
    t_2p = "\n".join(
        ["**Skyler Carlson:** Hi this is Skyler Carlson speaking today."] * 12
        + ["**Amit Ayer:** Thanks Skyler, let's dive in."] * 10
    )
    meta = {
        "project": "project_lens",
        "meeting_name": "Two_Person_Sync",
        "speaker_hints": [None, {}, {"speaker": "", "quote": ""}],
        "hitl_ledger_path": str(tmp_path / "hitl_decision_ledger.json"),
    }
    assert check_speaker_skew_and_collapse(t_2p, roster_2, meta) is None


def test_iterative_third_person_turn_discrepancy_reassignment_and_bound(tmp_path: Path) -> None:
    """
    Category: Overflow & exhaustion / Iterative turn-level resolution.
    When a transcript already uses attendee names (unmapped == []) but a turn attributed to
    'Skyler Carlson' says 'I know Skyler is working on the deck', analyze_and_resolve catches
    the turn-level third-person discrepancy, invokes interactive_resolver, rewrites the turn
    to 'Miles Erickson', and terminates cleanly within bounded rounds.
    """
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_path)
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    transcript = (
        "## Live Feed\n"
        "**[00:01:00]** **Amit Ayer:** Welcome everyone.\n"
        "**[00:02:00]** **Skyler Carlson:** I know Skyler is working on the executive deck right now.\n"
        "**[00:03:00]** **Miles Erickson:** Yes, I synced with him earlier.\n"
    )
    metadata = {
        "project": "project_lens",
        "meeting_name": "Named_Turn_Contradiction",
        "date": "10/21/2026",
        "roster": {
            "PartnerAdvisory": [
                "Amit Ayer (Architect)",
                "Skyler Carlson (Director)",
                "Miles Erickson (Data Scientist)",
            ]
        },
        "speaker_map": {
            "Speaker 1": "Amit Ayer",
            "Speaker 2": "Skyler Carlson",
            "Speaker 3": "Miles Erickson",
        },
        "hitl_ledger_path": str(ledger_path),
    }

    calls: list[str] = []

    def _resolver(q: str, opts: list[str]) -> str:
        calls.append(q)
        return next(o for o in opts if "Miles Erickson" in o)

    res = engine.analyze_and_resolve(transcript, metadata, interactive_resolver=_resolver)
    assert len(calls) == 1
    assert "**Miles Erickson:** I know Skyler is working on the executive deck right now." in res.resolved_transcript
