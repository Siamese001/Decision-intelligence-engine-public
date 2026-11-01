"""
Wave 4 Adversarial & Edge-Case Tests: Speaker Attribution Regex, Per-Turn Hint Scoring, & HITL Diarization.
Covers >= 4 mandatory edge-case categories:
1. Boundary payload (transcript start/end boundary turns with < 2 preceding/following turns in review_contexts)
2. Malformed input (corrupted speaker_hints_queue.json, generic-to-generic polluted speaker_map)
3. Invariant bypass attempt (duplicate attendee assignment across clusters blocked by assert_injective_speaker_mapping)
4. Exact production regression (full-transcript bag-of-words collision rejected when quote words span multiple turns)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_attribution_core.models import SpeakerAttributionCollisionError
from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_boundary_first_and_last_turn_2_turn_review_context_markers(tmp_path: Path) -> None:
    """
    Category: Boundary payload.
    When the unresolved target speaker appears on the very first turn (0 preceding turns)
    and very last turn (0 following turns), HITLRequiredError must still build valid
    boundary-annotated review_contexts that pass validate_visible_speaker_context.
    """
    transcript = "\n\n".join([
        "**Speaker 1** [00:00:01]: Very first turn at boundary.",
        "**Speaker 2** [00:00:05]: Middle turn one.",
        "**Speaker 2** [00:00:10]: Middle turn two.",
        "**Speaker 1** [00:00:15]: Very last turn at boundary.",
    ])
    meta: Dict[str, Any] = {
        "project": "NEE",
        "meeting_name": "Boundary Sync",
        "date": "10-16-2026",
        "attendees": [
            {"name": "Amit Ayer", "role": "MD", "org": "PartnerAdvisory"},
            {"name": "Eduardo More", "role": "Director", "org": "NEE"},
        ],
    }
    engine = AudioSpeakerAttributionEngine(
        decision_store=HITLDecisionStore(ledger_path=tmp_path / "ledger.json")
    )
    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, meta)

    err = exc_info.value
    assert len(err.review_contexts) == 2
    assert err.review_contexts[0]["before_unavailable"] == 2
    assert err.review_contexts[1]["after_unavailable"] == 2
    validate_visible_speaker_context(err.prompt)


def test_production_regression_cross_turn_bag_of_words_never_matches_quote_hint() -> None:
    """
    Category: Exact production regression (BUG-17).
    Even when 100% of a quote hint's words appear across different turns of Speaker 1,
    _check_quote_hint_match must NOT match Speaker 1 if no single turn has >= 65% overlap.
    """
    transcript = "\n\n".join([
        "**Speaker 1** [00:00:01]: We want to review the schedule.",
        "**Speaker 1** [00:00:05]: Later we will have Maximo deployed.",
        "**Speaker 1** [00:00:10]: Everything is populated with clean data.",
        "**Speaker 1** [00:00:15]: That aligns the hierarchy across teams.",
        "**Speaker 2** [00:00:20]: General comment without hint words.",
    ])
    hints = [
        {
            "speaker": "Eduardo More",
            "quote": "We want to have Maximo populated with the hierarchy",
            "status": "QUEUED",
        }
    ]
    roster = [("Amit Ayer", "PartnerAdvisory"), ("Eduardo More", "NEE")]
    meta: Dict[str, Any] = {"speaker_hints": hints, "attendees": ["Amit Ayer", "Eduardo More"]}

    cands = score_candidates_for_speaker("Speaker 1", transcript, roster, meta, total_clusters=2)
    eduardo_cand = next(c for c in cands if c.attendee_name == "Eduardo More")
    assert eduardo_cand.confidence == 0.50
    assert "Operator quote hint matched" not in eduardo_cand.evidence_rationale


def test_polluted_generic_speaker_map_ignored_by_u0_and_attribution_engine(tmp_path: Path) -> None:
    """
    Category: Malformed / polluted state recovery (BUG-15).
    If metadata['speaker_map'] contains generic-to-generic crosswalk entries
    ({'SPEAKER_00': 'Speaker 1', 'SPEAKER_01': 'Speaker 2'}), U0 and AudioSpeakerAttributionEngine
    must ignore those generic values rather than treating the clusters as mapped or raising UnmappedSpeakerError.
    """
    transcript = "\n\n".join([
        "**Speaker 1** [00:00:01]: Welcome everyone.",
        "**Speaker 2** [00:00:05]: 93 reviewed, 62 rejected in the batch.",
    ])
    meta: Dict[str, Any] = {
        "project": "NEE",
        "meeting_name": "Polluted Map Test",
        "date": "10-16-2026",
        "speaker_map": {"SPEAKER_00": "Speaker 1", "SPEAKER_01": "Speaker 2"},
        "speaker_hints": [{"speaker": "Eduardo More", "quote": "93 reviewed, 62 rejected"}],
        "attendees": [
            {"name": "Amit Ayer", "role": "MD", "org": "PartnerAdvisory"},
            {"name": "Eduardo More", "role": "Director", "org": "NEE"},
        ],
    }
    assert U0IngestionBoundary._detect_diarization_status(transcript, meta) == "DIARIZED_ACOUSTIC"

    engine = AudioSpeakerAttributionEngine(
        decision_store=HITLDecisionStore(ledger_path=tmp_path / "ledger.json")
    )
    res = engine.analyze_and_resolve(transcript, meta)
    assert res.speaker_map["Speaker 2"] == "Eduardo More"
    assert res.speaker_map["Speaker 1"] == "Amit Ayer"


def test_invariant_bypass_duplicate_speaker_assignment_blocked() -> None:
    """
    Category: Invariant bypass attempt.
    assert_injective_speaker_mapping must block duplicate assignment of the same attendee
    to two distinct speaker clusters when cluster_count <= roster_count.
    """
    with pytest.raises(SpeakerAttributionCollisionError):
        AudioSpeakerAttributionEngine.assert_injective_speaker_mapping(
            {"Speaker 1": "Eduardo More", "Speaker 2": "Eduardo More"},
            roster_count=2,
            cluster_count=2,
        )
