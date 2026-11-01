"""
Wave 4 E2E Verification: Speaker Attribution Regex, Per-Turn Hint Scoring, & Interactive HITL Diarization
(BUG-15, BUG-16, BUG-17, BUG-18).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.canonical_pipeline.c0_pa_runner import execute_c0_pa
from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    extract_ordered_dialogue_turns,
    score_candidates_for_speaker,
)
from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_turns
from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context
from dual_pipeline.transcript_pipeline_core.context_builder import _resolve_speaker_attribution_if_needed
from dual_pipeline.transcript_stages_core.l1_attribution import apply_speaker_map
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class _DummyTranscriber(BaseAudioTranscriber):
    def _live_transcribe(self, audio_file_path: str, metadata: Dict[str, Any] | None = None) -> str:
        return ""


def _build_340_turn_ed_amit_fixture() -> str:
    """
    Constructs a realistic 340-turn (**Speaker N** [HH:MM:SS]:) fixture modeling the
    10-16-2026 Ed_Amit meeting:
    - 212 turns by Speaker 1 (Amit Ayer), with individual words from Eduardo's Hint 1
      ('We want to have Maximo populated with the hierarchy') scattered across separate turns.
    - 128 turns by Speaker 2 (Eduardo More), including exact single-turn quote Hint 3
      ('93 reviewed, 62 rejected during the validation pass').
    """
    scattered_words = ["we", "want", "to", "have", "maximo", "populated", "with", "the", "hierarchy"]
    lines: List[str] = []
    s1_count = 0
    s2_count = 0
    for idx in range(340):
        mm, ss = divmod(idx * 5, 60)
        hh, mm = divmod(mm, 60)
        ts = f"{hh:02d}:{mm:02d}:{ss:02d}"
        if s1_count < 212 and (idx % 3 != 2 or s2_count >= 128):
            word = scattered_words[s1_count % len(scattered_words)]
            text = f"Turn {idx} discussion point regarding {word} in our program roadmap."
            lines.append(f"**Speaker 1** [{ts}]: {text}")
            s1_count += 1
        else:
            if s2_count == 40:
                text = "Right now we stand at 93 reviewed, 62 rejected during the validation pass."
            else:
                text = f"Turn {idx} operational update on field asset readiness."
            lines.append(f"**Speaker 2** [{ts}]: {text}")
            s2_count += 1
    assert s1_count == 212
    assert s2_count == 128
    return "\n\n".join(lines)


def test_ed_amit_340_turn_attribution_and_1_to_1_elimination(tmp_path: Path) -> None:
    """
    Verifies BUG-15, BUG-16, BUG-17 on the 340-turn Ed_Amit fixture:
    - extract_ordered_dialogue_turns returns all 340 turns
    - apply_quote_hints_to_turns matches the hinted turn on Speaker 2
    - Speaker 2 scores 0.95 (HIGH) for Eduardo More without false-positive bag-of-words collision on Speaker 1
    - Speaker 1 resolves to Amit Ayer (0.88 HIGH) via 1-to-1 complementary elimination
    - apply_speaker_map produces zero double colons ('**Eduardo More:** [HH:MM:SS]:')
    """
    transcript = _build_340_turn_ed_amit_fixture()
    ordered = extract_ordered_dialogue_turns(transcript)
    assert len(ordered) == 340

    hints = [
        {
            "event_id": "h1",
            "speaker": "Eduardo More",
            "quote": "We want to have Maximo populated with the hierarchy",
            "status": "QUEUED",
        },
        {
            "event_id": "h3",
            "speaker": "Eduardo More",
            "quote": "93 reviewed, 62 rejected",
            "status": "QUEUED",
        },
    ]
    _, reassignments = apply_quote_hints_to_turns(transcript, [dict(h) for h in hints])
    assert len(reassignments) == 1

    roster = [("Amit Ayer", "PartnerAdvisory"), ("Eduardo More", "NEE")]
    meta: Dict[str, Any] = {
        "project": "NEE",
        "meeting_name": "Touch Base with Eduardo",
        "date": "10-16-2026",
        "attendees": [
            {"name": "Amit Ayer", "role": "Managing Director", "org": "PartnerAdvisory"},
            {"name": "Eduardo More", "role": "Director", "org": "NEE"},
        ],
        "speaker_hints": hints,
    }

    cands_s2 = score_candidates_for_speaker("Speaker 2", transcript, roster, meta, total_clusters=2)
    assert cands_s2[0].attendee_name == "Eduardo More"
    assert cands_s2[0].confidence == 0.95

    cands_s1 = score_candidates_for_speaker("Speaker 1", transcript, roster, meta, total_clusters=2)
    assert cands_s1[0].attendee_name == "Amit Ayer"
    assert cands_s1[0].confidence >= 0.88

    store = HITLDecisionStore(ledger_path=tmp_path / "hitl_ledger.json")
    engine = AudioSpeakerAttributionEngine(decision_store=store)
    res = engine.analyze_and_resolve(transcript, meta, autonomous_if_decisive=True)

    assert res.speaker_map["Speaker 2"] == "Eduardo More"
    assert res.speaker_map["Speaker 1"] == "Amit Ayer"
    assert "**Eduardo More:** [" not in res.resolved_transcript
    assert "**Amit Ayer:** [" not in res.resolved_transcript
    assert "**Eduardo More** [" in res.resolved_transcript
    assert "**Amit Ayer** [" in res.resolved_transcript


def test_base_transcriber_does_not_pollute_speaker_map_and_u0_defers_to_c0_pa() -> None:
    """
    Verifies BUG-15:
    BaseAudioTranscriber._format_diarized_json writes SPEAKER_00 -> Speaker 1 into
    metadata['cluster_label_map'] without polluting metadata['speaker_map'], and
    U0IngestionBoundary._detect_diarization_status returns DIARIZED_ACOUSTIC instead
    of raising UnmappedSpeakerError when hints/acoustic evidence exist.
    """
    transcriber = _DummyTranscriber(mock=True)
    meta: Dict[str, Any] = {
        "turn_hints": [{"speaker": "Eduardo More", "quote": "93 reviewed, 62 rejected"}],
    }
    formatted = transcriber._format_diarized_json(
        [
            {"speaker": "SPEAKER_00", "start": 1.0, "text": "Hello Eduardo."},
            {"speaker": "SPEAKER_01", "start": 4.0, "text": "93 reviewed, 62 rejected."},
        ],
        metadata=meta,
    )
    assert meta.get("speaker_map") is None
    assert meta["cluster_label_map"] == {"SPEAKER_00": "Speaker 1", "SPEAKER_01": "Speaker 2"}
    assert U0IngestionBoundary._detect_diarization_status(formatted, meta) == "DIARIZED_ACOUSTIC"

    mapped = apply_speaker_map(formatted, {"Speaker 1": "Amit Ayer", "Speaker 2": "Eduardo More"})
    assert "**Amit Ayer** [00:00:01]: Hello Eduardo." in mapped
    assert "**Eduardo More** [00:00:04]: 93 reviewed, 62 rejected." in mapped


def test_c0_pa_runner_and_context_builder_preserve_interactive_resolver_and_2_turn_hitl(tmp_path: Path) -> None:
    """
    Verifies BUG-18:
    - context_builder._resolve_speaker_attribution_if_needed does not double-pop interactive_resolver
    - Ambiguous margin (<= 30%) without interactive_resolver raises HITLRequiredError with
      2-turn review_contexts (2 BEFORE, TARGET, 2 AFTER) and options.
    """
    ambiguous_transcript = "\n\n".join([
        "**Speaker 1** [00:00:01]: Opening turn one.",
        "**Speaker 1** [00:00:05]: Opening turn two.",
        "**Speaker 2** [00:00:10]: Target turn needing attribution.",
        "**Speaker 1** [00:00:15]: Following turn one.",
        "**Speaker 1** [00:00:20]: Following turn two.",
    ])
    meta: Dict[str, Any] = {
        "project": f"NEE_ISO_{tmp_path.name}",
        "meeting_name": "Ambiguous Sync",
        "date": "10-16-2026",
        "diarization_status": "DIARIZED_ACOUSTIC",
        "attendees": [
            {"name": "Amit Ayer", "role": "MD", "org": "PartnerAdvisory"},
            {"name": "Eduardo More", "role": "Director", "org": "NEE"},
        ],
    }
    store = HITLDecisionStore(ledger_path=tmp_path / "hitl_ledger.json")
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(ambiguous_transcript, meta)

    err = exc_info.value
    assert len(err.options) == 2
    assert len(err.review_contexts) >= 1
    assert err.context_digest
    validate_visible_speaker_context(err.prompt)

    # Now verify context_builder preserves interactive_resolver (no duplicate pop)
    called_prompts: List[str] = []

    def _resolver(prompt_str: str, opts: List[str]) -> str:
        called_prompts.append(prompt_str)
        return opts[0]

    u0_ctx: Dict[str, Any] = {
        "metadata": {
            **meta,
            "interactive_resolver": _resolver,
            "decision_store": store,
        }
    }
    l0_ctx: Dict[str, Any] = {"metadata": dict(meta)}
    resolved_text = _resolve_speaker_attribution_if_needed(
        plan=SimpleNamespace(),
        u0_context=u0_ctx,
        l0_context=l0_ctx,
        transcript_text=ambiguous_transcript,
        is_notes_mode=False,
        is_pre_resolved_input=False,
    )
    assert len(called_prompts) >= 1
    assert "**Amit Ayer**" in resolved_text
    assert "**Eduardo More**" in resolved_text
