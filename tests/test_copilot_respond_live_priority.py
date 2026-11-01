"""Unit tests for live-first respond priority and gated Whisper lexical priming.

Verifies:
1. eval_respond prioritizes live speaker turns over historical graph assertions.
2. Uncorroborated historical assertions with zero topic overlap are suppressed.
3. build_whisper_prompt for generic syncs excludes technical acronyms (CIP-005, NERC-CIP).
4. build_whisper_prompt includes technical acronyms when corroborated by meeting topic.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_intents import eval_respond
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.lexical_priming import LexicalPrimer


class MockDialogueWithLiveTurns:
    """Mock dialogue context providing live speaker turns."""

    def __init__(self, turns: List[Dict[str, Any]], query: str = "") -> None:
        self._turns = turns
        self._raw_request = query

    def focus_turns(self) -> List[Dict[str, Any]]:
        return list(self._turns)

    def retrieval_seeds(self) -> List[str]:
        return ["loss leader", "commercial model"]


def _build_test_graph() -> Any:
    """Builds a test topology with Drew Oakley and both relevant and uncorroborated assertions."""
    nodes = {
        "person_dan_okray": {
            "node_id": "person_dan_okray",
            "canonical_name": "Drew Oakley",
            "display_name": "Drew Oakley",
            "node_type": "person",
            "engagement_id": "project:project_nexus",
            "role": "Managing Director",
            "organization": "PartnerAdvisory",
        },
        "concept:loss_leader": {
            "node_id": "concept:loss_leader",
            "canonical_name": "Loss Leader Commercial Model",
            "node_type": "concept",
            "engagement_id": "project:project_nexus",
        },
    }
    assertions = {
        # Corroborated assertion (mentions loss leader)
        "asrt_corroborated": {
            "assertion_id": "asrt_corroborated",
            "subject_node_id": "person_dan_okray",
            "object_node_id": "concept:loss_leader",
            "predicate": "PROPOSES",
            "claim_text": "Drew Oakley proposed building the commercial loss leader model for Nexus.",
            "engagement_id": "project:project_nexus",
            "canonical_status": "canonical",
            "retrieval_eligible": 1,
            "canonical_eligible": 1,
        },
        # Uncorroborated historical assertion with zero lexical overlap with live turns
        "asrt_uncorroborated": {
            "assertion_id": "asrt_uncorroborated",
            "subject_node_id": "person_dan_okray",
            "object_node_id": "concept:legacy_billing",
            "predicate": "OWNS",
            "claim_text": "Drew Oakley managed legacy billing reconciliation spreadsheets.",
            "engagement_id": "project:project_nexus",
            "canonical_status": "canonical",
            "retrieval_eligible": 1,
            "canonical_eligible": 1,
        },
    }
    return SimpleNamespace(nodes=nodes, assertions=assertions, forward_adjacency={})


def test_eval_respond_prioritizes_live_turns_and_drops_uncorroborated() -> None:
    """Verifies that live speaker turns lead and uncorroborated historical claims are dropped."""
    graph = _build_test_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:project_nexus",
        attendees=[{"name": "Drew Oakley", "role": "Managing Director", "org": "PartnerAdvisory"}],
    )

    live_turns = [
        {
            "speaker": "Drew Oakley",
            "text": "We need to structure the commercial model as a loss leader to build initial velocity.",
            "start_time": 1285.0,
            "end_time": 1300.0,
        },
        {
            "speaker": "Cameron Mercer",
            "text": "That sounds like a sensible strategy for the initial phase.",
            "start_time": 1301.0,
            "end_time": 1304.0,
        },
        {
            "speaker": "Drew Oakley",
            "text": "The key is getting alignment on the investment model before scaling.",
            "start_time": 1305.0,
            "end_time": 1320.0,
        },
    ]
    dialogue = MockDialogueWithLiveTurns(
        turns=live_turns,
        query="respond to Dan's point about loss leader and building the model",
    )

    response_text, assertions, ok = eval_respond(
        retriever=retriever,
        target="Dan",
        dialogue=dialogue,
    )

    assert ok is True
    assert len(assertions) > 0

    # Top assertions must be live speaker turns
    assert assertions[0].get("is_live_turn") is True, "First assertion must be a live speaker turn"
    live_claims = [a.get("claim_text", "").lower() for a in assertions if a.get("is_live_turn")]
    assert any("loss leader" in c for c in live_claims), "Live speaker turns must include the loss leader point"

    # Uncorroborated historical assertion must be suppressed
    retrieved_ids = [a.get("assertion_id") for a in assertions]
    assert "asrt_uncorroborated" not in retrieved_ids, "Uncorroborated historical assertion must be dropped"


def test_build_whisper_prompt_gates_narrow_technical_acronyms() -> None:
    """Verifies narrow technical acronyms (CIP-005, NERC-CIP) are gated behind context."""
    primer = LexicalPrimer("project_nexus")
    attendees = [
        {"name": "Cameron Mercer"},
        {"name": "Drew Oakley"},
        {"name": "Amit Ayer"},
        {"organization": "PartnerAdvisory"},
    ]

    # Case 1: Generic commercial sync meeting
    generic_prompt = primer.build_whisper_prompt(
        meeting_name="Stepfan, Dan, Amit Sync",
        attendees=attendees,
    )
    # Must include attendees
    assert "Cameron Mercer" in generic_prompt
    assert "Drew Oakley" in generic_prompt
    assert "Amit Ayer" in generic_prompt
    assert "PartnerAdvisory" in generic_prompt
    # Must NOT include narrow technical acronyms
    assert "CIP-005" not in generic_prompt
    assert "NERC-CIP" not in generic_prompt
    assert "SCADA" not in generic_prompt

    # Case 2: Technical NERC-CIP compliance meeting
    technical_prompt = primer.build_whisper_prompt(
        meeting_name="NERC-CIP Substation Security & CIP-005 Review",
        attendees=attendees,
    )
    # Must include NERC-CIP and CIP-005 when corroborated
    assert "CIP-005" in technical_prompt or "NERC-CIP" in technical_prompt
