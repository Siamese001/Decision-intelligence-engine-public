"""End-to-end speaker question lifecycle with visible source context and isolated ledger."""

from types import SimpleNamespace

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.speaker_disambiguation import _prompt_speaker_resolution


def test_question_contains_five_source_turns_before_persisted_assignment(tmp_path):
    seen = {}
    turns = [
        {"turn_id": str(index), "source_position": index, "source_id": "meeting-qa",
         "speaker": "Speaker 1" if index == 2 else "Avery", "timestamp": f"00:00:0{index}",
         "text": f"source statement {index}"}
        for index in range(5)
    ]
    turn = SimpleNamespace(
        timestamp="00:00:02", turn_index=2, assigned_speaker="Speaker 1", confidence=0.61,
        second_candidate="Avery", second_confidence=0.39, margin_delta=0.22, text_snippet="source statement 2",
    )
    options = [
        {"key": "Avery", "label": "Attribute to Avery", "confidence": 0.61},
        {"key": "Jordan", "label": "Attribute to Jordan", "confidence": 0.39},
    ]
    store = HITLDecisionStore(ledger_path=tmp_path / "ledger.json", sqlite_path=tmp_path / "ledger.sqlite")

    selected, receipt = _prompt_speaker_resolution(
        turn,
        options,
        store,
        lambda envelope: seen.setdefault("payload", envelope.to_ask_user_question_payload()) or "Avery",
        {"_speaker_review_turns": turns, "_speaker_review_source_id": "meeting-qa"},
    )

    question = seen["payload"]["questions"][0]["question"]
    assert all(f"source statement {index}" in question for index in range(5))
    assert "TARGET [00:00:02] **UNRESOLVED — Speaker 1**" in question
    assert selected == "Avery"
    assert receipt["receipt_digest"]
