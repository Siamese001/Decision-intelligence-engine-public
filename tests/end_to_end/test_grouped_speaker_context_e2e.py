"""Isolated grouped speaker review and context-bound approval lifecycle."""
from dual_pipeline.speaker_attribution_core.review_context import SpeakerReviewContextError
from dual_pipeline.speaker_attribution_core.review_units import ReviewDecision, ReviewUnitManager


def test_grouped_review_serializes_five_turns_and_binds_approval():
    utterances = [
        {"utterance_id": f"u{i}", "speaker": f"SPEAKER_{i % 2}", "text": f"Verbatim turn {i}.",
         "start_time_sec": float(i), "end_time_sec": float(i + 1), "word_ids": [f"w{i}"]}
        for i in range(6)
    ]
    manager = ReviewUnitManager()
    item = next(x for x in manager.generate_review_items(utterances) if "u2" in x.target_utterance_ids)
    payload = item.to_dict()
    assert len(payload["review_contexts"]) == len(item.target_utterance_ids)
    assert "BEFORE 2" in item.visible_context() and "AFTER 2" in item.visible_context()

    # Validate that visible context strictly obeys row-separation rules and passes hook
    import json
    from tools.check_ask_user_question import check_hook_payload
    from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context

    visible_ctx = item.visible_context()
    assert validate_visible_speaker_context(visible_ctx)

    hook_payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "question": f"{visible_ctx}\n\nWho is speaking in the TARGET turn?",
                        "options": [
                            "(Recommended) Amit Ayer (Confidence: HIGH (90%)) — Target speaker.",
                            "Other Speaker (Confidence: LOW (10%)) — Alternative.",
                        ],
                    }
                ]
            },
        }
    }
    hook_decision = check_hook_payload(json.dumps(hook_payload))
    assert hook_decision["decision"] == "allow"

    decision = ReviewDecision("d1", item.review_id, "confirm_all", "Amit Ayer",
                              item.target_utterance_ids, item.word_ids, context_digest=item.context_digest)
    result = manager.apply_decision(decision, [item], utterances)
    assert result["modified_utterances"] == len(item.target_utterance_ids)
    assert all(utterances[int(uid[1:])]["speaker"] == "Amit Ayer" for uid in item.target_utterance_ids)


def test_grouped_review_missing_text_fails_closed():
    utterances = [{"utterance_id": "u1", "speaker": "SPEAKER_1", "word_ids": []}]
    item = ReviewUnitManager().generate_review_items(utterances)[0]
    try:
        item.visible_context()
    except SpeakerReviewContextError:
        pass
    else:
        raise AssertionError("Context without verbatim text must not be reviewable")


def test_grouped_review_collapsed_context_rejected_by_hook_and_validator():
    import json
    import pytest
    from tools.check_ask_user_question import check_hook_payload
    from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context

    utterances = [
        {"utterance_id": f"u{i}", "speaker": f"SPEAKER_{i % 2}", "text": f"Verbatim turn {i}.",
         "start_time_sec": float(i), "end_time_sec": float(i + 1), "word_ids": [f"w{i}"]}
        for i in range(6)
    ]
    manager = ReviewUnitManager()
    item = next(x for x in manager.generate_review_items(utterances) if "u2" in x.target_utterance_ids)
    clean_ctx = item.visible_context()

    # Simulate buggy caller or regex that collapses newlines to spaces
    collapsed_ctx = " ".join(clean_ctx.splitlines())
    with pytest.raises(ValueError, match="multiple turn markers .* collapsed onto the same line"):
        validate_visible_speaker_context(collapsed_ctx)

    bad_payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "question": f"{collapsed_ctx}\n\nWho is speaking in the TARGET turn?",
                        "options": [
                            "(Recommended) Amit Ayer (Confidence: HIGH (90%)) — Target speaker.",
                            "Other Speaker (Confidence: LOW (10%)) — Alternative.",
                        ],
                    }
                ]
            },
        }
    }
    deny_res = check_hook_payload(json.dumps(bad_payload))
    assert deny_res["decision"] == "deny"
    assert "collapsed onto the same line" in deny_res["reason"]

