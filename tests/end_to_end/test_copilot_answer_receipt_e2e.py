
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    CopilotAnswerLedgerAdapter,
    ensure_inferred_memory_boundary,
)


def test_answer_receipt_schema_validates():
    feedback = {
        "category": "STRATEGIC",
        "snap_cue": "Verify LENS SLA timeline with client.",
        "suggested_script": "I recommend confirming the October target date.",
        "verified_assertions": [{"assertion_id": "asrt_01"}],
        "rigor_tier": "FAST",
    }
    ledger_id = CopilotAnswerLedgerAdapter.record_delivery_safe(feedback, session_id="test_sess_01")
    assert ledger_id.startswith("ans_copilot_")
    assert feedback["ledger_id"] == ledger_id


def test_ws_feedback_creates_ledger_row():
    raw_payload = {"flow_summary": "Sprint discussion", "entity_scores": {"amit": 0.9}}
    bounded = ensure_inferred_memory_boundary(raw_payload)
    assert bounded["is_inferred"] is True
    assert bounded["is_canonical"] is False
    assert bounded["provenance"] == "conversational_inference"

