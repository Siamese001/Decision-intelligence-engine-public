"""
tests/test_ingest_seam_c19_copilot_adversarial.py

Adversarial and edge-case test suite for Wave 7:
1. Invariant bypass attempt: Pre-egress C-19, secret, billing rate, and INTERNAL_ADVISORY scrubbing before writing segments.jsonl and live_transcript.md.
2. Boundary payload & invariant bypass: Copilot 0-turn target returns ZERO_ATTRIBUTED_TURNS_FOR_TARGET under strict_target and never relabels Host/SPEAKER_00 turns to target.
3. Malformed input: Nested dict collateral_drop_log.json ({"drops": [...]}) unpacked cleanly by detector and collateral healer.
4. Concurrency & race: Idempotent CollateralFsWatcher.start() prevents duplicate polling threads.
5. Exact production regression: Third-person mention inside acoustically/stereo-bound speaker cluster does not evict or flip the speaker binding.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.attachment_core.detector import _load_drop_log
from dual_pipeline.live_meeting_recorder_core.collateral_watcher import CollateralFsWatcher
from dual_pipeline.live_meeting_recorder_core.copilot_context import UtteranceSelector
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
    _resolve_speaker_and_utterance,
)
from dual_pipeline.sanitizer import LocalPreEgressSanitizer
from dual_pipeline.speaker_attribution_core.conflict_detector import (
    detect_third_person_contradictions_in_turns,
    reconcile_existing_map_conflicts,
)
from dual_pipeline.transcript_qa_core.collateral_healer import _load_drop_log_attachments


def test_pre_egress_sanitizer_scrubs_c19_secrets_and_billing_rates() -> None:
    raw = (
        "Our career leverage and executive compensation band depend on keeping key sk_test_redacted_placeholder "
        "and our $250/hr billing rate out of INTERNAL_ADVISORY logs."
    )
    scrubbed = LocalPreEgressSanitizer.scrub_text(raw)
    assert "career leverage" not in scrubbed.lower()
    assert "executive compensation band" not in scrubbed.lower()
    assert "sk-proj1234567890" not in scrubbed
    assert "$250/hr" not in scrubbed
    assert "INTERNAL_ADVISORY" not in scrubbed
    assert "[REDACTED_C19]" in scrubbed


def test_copilot_strict_zero_turns_and_no_placeholder_relabeling() -> None:
    turns = [
        {"seq": 1, "speaker": "Host", "text": "We need to finalize the vector migration architecture.", "turn_sec": 5.0},
        {"seq": 2, "speaker": "SPEAKER_01", "text": "The latency budget is under 20 milliseconds.", "turn_sec": 12.0},
    ]
    # Strict target mode must refuse to fabricate turns from Host/SPEAKER_01 when target has 0 turns
    strict_res = UtteranceSelector.select(
        turns, target_speaker="Daniel Sabet", topic_keywords=["vector", "migration"], strict_target=True
    )
    assert strict_res.status == "NO_SPEAKER_TURNS"
    assert strict_res.error_code == "ZERO_ATTRIBUTED_TURNS_FOR_TARGET"

    # Even in non-strict mode, _resolve_speaker_and_utterance must NEVER overwrite "Host" to "Daniel Sabet"
    ctx = ConversationContextModel(attendees=["Amit Ayer", "Daniel Sabet"])
    ctx.add_turn(speaker="Host", text="We need to finalize the vector migration architecture.", timestamp="00:00:05", turn_sec=5.0)
    res_target, utt, _, err = _resolve_speaker_and_utterance(
        target="Daniel Sabet", topic_keywords=["vector"], context_model=ctx, in_mem=None
    )
    assert err is None
    assert utt is not None
    assert utt.speaker == "Host"


def test_collateral_drop_log_nested_dict_unwrapped_cleanly(tmp_path: Path) -> None:
    out_dir = tmp_path / "output_session"
    ref_dir = out_dir / "referenced_artifacts"
    ref_dir.mkdir(parents=True, exist_ok=True)
    slide_p = ref_dir / "architecture_deck.pdf"
    slide_p.write_bytes(b"%PDF-1.4 mock slide deck")

    nested_payload = {
        "session_id": "sess-01",
        "drops": [
            {
                "drop_event_id": "drop-0001-abcd1234",
                "filename": "architecture_deck.pdf",
                "dropped_at_elapsed": "00:04:15",
                "sha256": "abcd1234" * 8,
                "attribution_mode": "COLLATERAL_REVIEWED",
                "source": "live_drop",
            }
        ],
    }
    (out_dir / "collateral_drop_log.json").write_text(json.dumps(nested_payload), encoding="utf-8")

    by_sha, by_fname = _load_drop_log(out_dir, ref_dir)
    assert "architecture_deck.pdf" in by_fname
    assert by_fname["architecture_deck.pdf"]["dropped_at_elapsed"] == "00:04:15"

    healer_atts = _load_drop_log_attachments({"output_dir": str(out_dir)})
    assert len(healer_atts) == 1
    assert healer_atts[0]["filename"] == "architecture_deck.pdf"


def test_collateral_watcher_start_is_idempotent(tmp_path: Path) -> None:
    ref_dir = tmp_path / "referenced_artifacts"
    watcher = CollateralFsWatcher(ref_dir=ref_dir, get_elapsed_fn=lambda: 10.0)
    try:
        watcher.start()
        first_thread = watcher._thread
        assert first_thread is not None and first_thread.is_alive()
        watcher.start()
        assert watcher._thread is first_thread
    finally:
        watcher.stop()


def test_third_person_mention_does_not_evict_acoustically_bound_speaker() -> None:
    roster = [
        {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead"},
        {"name": "Daniel Sabet", "org": "Apex Global", "role": "Architect"},
    ]
    transcript = (
        "**[00:00:10] Speaker 1:** What Daniel was working on yesterday is ready for deployment.\n"
        "**[00:00:20] Speaker 2:** Great, let us ship it.\n"
    )
    existing_map = {"Speaker 1": "Daniel Sabet", "Speaker 2": "Amit Ayer"}
    # When Speaker 1 is bound via voiceprint_matches or VERIFIED_STEREO_SEPARATION, third-person regex must not evict it
    meta_bound = {
        "speaker_map": dict(existing_map),
        "voiceprint_matches": {"Speaker 1": "Daniel Sabet", "Speaker 2": "Amit Ayer"},
        "diarization_status": "VERIFIED_STEREO_SEPARATION",
    }
    turns = [("Speaker 1", "What Daniel was working on yesterday is ready for deployment."), ("Speaker 2", "Great, let us ship it.")]
    assert detect_third_person_contradictions_in_turns(turns, roster, meta_bound) == []
    evicted = reconcile_existing_map_conflicts(transcript, transcript, existing_map, roster, meta_bound)
    assert evicted == []
    assert existing_map["Speaker 1"] == "Daniel Sabet"
