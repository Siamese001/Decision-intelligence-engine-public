"""Fail-closed controls for grouped speaker review context and approvals."""
import pytest

from dual_pipeline.speaker_attribution_core.review_context import SpeakerReviewContextError
from dual_pipeline.speaker_attribution_core.review_units import ReviewDecision, ReviewUnitManager


def test_missing_or_stale_context_and_wrong_digest_never_mutate_speakers():
    utterances = [
        {"utterance_id": f"u{i}", "speaker": "SPEAKER_1", "text": f"Turn {i}.", "word_ids": [f"w{i}"],
         "start_time_sec": i, "end_time_sec": i + 0.5}
        for i in range(3)
    ]
    manager = ReviewUnitManager()
    item = manager.generate_review_items(utterances)[0]
    before = [turn["speaker"] for turn in utterances]
    bad = ReviewDecision("d1", item.review_id, "confirm_all", "Amit Ayer", item.target_utterance_ids,
                         item.word_ids, context_digest="0" * 64)
    with pytest.raises(SpeakerReviewContextError):
        manager.apply_decision(bad, [item], utterances)
    assert [turn["speaker"] for turn in utterances] == before
    good = ReviewDecision("d2", item.review_id, "confirm_all", "Amit Ayer", item.target_utterance_ids,
                          item.word_ids, context_digest=item.context_digest)
    utterances[0]["text"] = "Edited after context display."
    with pytest.raises(SpeakerReviewContextError, match="stale"):
        manager.apply_decision(good, [item], utterances)
    assert [turn["speaker"] for turn in utterances] == before
