from dual_pipeline.live_meeting_recorder_core.copilot_validator import validate_copilot_feedback


def test_fabricated_assertion_id_rejected():
    parsed = {
        "has_guidance": True,
        "claims": [
            {"role": "FACT", "source_kind": "GRAPH_ASSERTION", "source_ref": "fake_123", "verbatim_span": "X owns Y"}
        ]
    }
    # Retrieved set is empty, so fake_123 is rejected
    res = validate_copilot_feedback(parsed, valid_assertion_ids=[])
    # The failed FACT claim is dropped or the contract is rejected
    assert res is not None
    assert len(res.get("claims", [])) == 0

def test_quote_substring_dropped():
    parsed = {
        "has_guidance": True,
        "claims": [
            {"role": "QUOTE", "source_kind": "LIVE_TURN", "source_ref": "turn_1", "verbatim_span": "I never said this"}
        ]
    }
    # The quote is not in the turn text
    res = validate_copilot_feedback(parsed, live_turns=[{"id": "turn_1", "text": "I said something else"}])
    assert res is not None
    assert len(res.get("claims", [])) == 0

def test_empty_graph_factual_owner_question_no_commitment():
    # Empty graph plus a factual owner question produces a question or has_guidance:false, never a commitment.
    parsed = {
        "has_guidance": True,
        "suggested_script": "A owns X.",
        "claims": []
    }
    res = validate_copilot_feedback(parsed, valid_assertion_ids=[])
    # Unsupported script is suppressed or replaced with a neutral question
    assert "owns" not in res.get("suggested_script", "").lower() or "?" in res.get("suggested_script", "")
