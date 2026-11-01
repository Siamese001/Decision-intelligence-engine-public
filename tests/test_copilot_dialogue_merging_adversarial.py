"""
tests/test_copilot_dialogue_merging_adversarial.py

Adversarial and edge-case verification for Live Copilot dialogue and topic merging.
Covers 4 mandatory categories:
1. Boundary payload: None dialogue, empty strings, empty keyword lists.
2. Malformed input: non-context objects (int, dict, generator) passed as dialogue.
3. Overflow & exhaustion: large turn volumes (1000+ turns), oversized topic strings.
4. Exact production regression: verifies the exact 'vector database' active meeting prompt.
"""
from __future__ import annotations

from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_intents import (
    BlendedDialogueContext,
    eval_actions,
    eval_questions,
    evaluate_intent,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


@pytest.fixture
def mock_topology() -> InMemoryGraphTopology:
    from pathlib import Path
    return InMemoryGraphTopology(
        graph_path=Path("mock.sqlite"),
        graph_sha256="mock_sha",
        nodes={},
        assertions={},
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency={},
        reverse_adjacency={},
        undirected_adjacency={},
        canonical_edges=[],
    )


class RecordingRetriever(CopilotGraphRetriever):
    """Test retriever tracking query tokens and parameters."""

    def __init__(self, topology: InMemoryGraphTopology) -> None:
        super().__init__(topology=topology, engagement_id="project:project_nexus")
        self.recorded_dialogues: List[Any] = []

    def get_next_questions(self, dialogue: Any, max_questions: int = 3) -> List[Dict[str, Any]]:
        self.recorded_dialogues.append(dialogue)
        return [
            {
                "assertion_id": "ast-reg-1",
                "predicate": "OPEN_QUESTION",
                "claim_text": "Vector database memory scaling requires partition bounds",
                "is_priority": True,
                "is_primary": True,
            }
        ]

    def get_next_actions(self, dialogue: Any, max_actions: int = 3) -> List[Dict[str, Any]]:
        self.recorded_dialogues.append(dialogue)
        return [
            {
                "assertion_id": "ast-reg-2",
                "predicate": "ACTION",
                "claim_text": "Establish persistent database cluster configuration",
                "is_priority": True,
                "is_primary": True,
            }
        ]


def test_adversarial_boundary_empty_and_none_payloads(
    mock_topology: InMemoryGraphTopology,
):
    """Category 1: Boundary payload tests (None, empty, whitespace-only)."""
    retriever = RecordingRetriever(mock_topology)

    # None dialogue, empty topic
    ctx1 = BlendedDialogueContext(dialogue=None, target=None, topic_query="", topic_keywords=[])
    assert ctx1.retrieval_seeds() == []
    assert ctx1.focus_turns() == []
    assert str(ctx1) == ""

    # Empty context model with no turns
    empty_model = ConversationContextModel(project_scope="project:project_nexus", attendees=[])
    ctx2 = BlendedDialogueContext(dialogue=empty_model, target=None, topic_query=None, topic_keywords=None)
    assert ctx2.focus_turns() == []
    assert ctx2.retrieval_seeds() == ["project:project_nexus"]

    body, assertions, ok = eval_questions(retriever, dialogue=None, topic_query="", topic_keywords=[])
    assert ok is True
    assert len(assertions) == 1


def test_adversarial_malformed_dialogue_types(
    mock_topology: InMemoryGraphTopology,
):
    """Category 2: Malformed input tests (non-standard dialogue objects)."""
    retriever = RecordingRetriever(mock_topology)

    # Int as dialogue
    ctx_int = BlendedDialogueContext(dialogue=12345, topic_query="storage")
    assert "storage" in ctx_int.retrieval_seeds()
    assert ctx_int.focus_turns() == []
    assert "12345" in str(ctx_int)

    # Dict as dialogue
    ctx_dict = BlendedDialogueContext(dialogue={"custom": "payload"}, topic_query="atlas")
    assert "atlas" in ctx_dict.retrieval_seeds()
    assert ctx_dict.focus_turns() == []

    # Generator / lambda as dialogue
    ctx_gen = BlendedDialogueContext(dialogue=(x for x in range(3)), target="Jason")
    assert "Jason" in ctx_gen.retrieval_seeds()
    assert ctx_gen.focus_turns() == []

    body, assertions, ok = eval_actions(retriever, dialogue={"key": "val"}, topic_query="database")
    assert ok is True
    assert len(assertions) == 1


def test_adversarial_overflow_and_exhaustion(
    mock_topology: InMemoryGraphTopology,
):
    """Category 3: Overflow & exhaustion tests (1000 turns, oversized queries)."""
    context_model = ConversationContextModel(project_scope="project:project_nexus", attendees=[])
    for i in range(1000):
        context_model.add_turn(
            speaker=f"Speaker_{i % 5}",
            text=f"Turn content {i} evaluating architectural constraints.",
            timestamp=f"{i // 60:02d}:{i % 60:02d}",
            turn_sec=float(i * 3.0),
        )

    huge_topic = " ".join(["vector_dimension"] * 200)
    huge_keywords = [f"kw_{k}" for k in range(150)]

    ctx = BlendedDialogueContext(
        dialogue=context_model,
        target="Speaker_1",
        topic_query=huge_topic,
        topic_keywords=huge_keywords,
    )

    seeds = ctx.retrieval_seeds()
    assert len(seeds) > 100
    assert "Speaker_1" in seeds

    focus = ctx.focus_turns()
    # Context model bounded focus turns
    assert len(focus) <= 20

    retriever = RecordingRetriever(mock_topology)
    body, assertions, ok = eval_questions(
        retriever, dialogue=context_model, topic_query=huge_topic, topic_keywords=huge_keywords
    )
    assert ok is True
    assert len(assertions) >= 1


def test_exact_production_regression_vector_database_dialogue(
    mock_topology: InMemoryGraphTopology,
):
    """Category 4: Exact production regression test from incident.

    Pin the scenario:
    Operator runs `nee copilot --active --intent next-questions --topic "vector database"`.
    Meeting transcript contains Voice A stating 'And you can always set up a Vector database.'
    Verifies that dialogue context with Voice A's turn is preserved in the blended retrieval query.
    """
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[
            {"name": "Amit Ayer", "role": "Lead Architect"},
            {"name": "Voice A", "role": "Speaker"},
        ],
    )
    context_model.add_turn(
        speaker="Voice A",
        text="And you can always set up a Vector database.",
        timestamp="07:40",
        turn_sec=460.0,
    )

    retriever = RecordingRetriever(mock_topology)

    body, assertions, ok = eval_questions(
        retriever=retriever,
        dialogue=context_model,
        topic_query="vector database",
        topic_keywords=["vector", "database"],
        target=None,
    )

    assert ok is True
    recorded = retriever.recorded_dialogues[-1]
    assert isinstance(recorded, BlendedDialogueContext)

    # Dialogue focus turns must include Voice A's statement
    focus = recorded.focus_turns()
    assert any("Vector database" in t.get("text", "") and t.get("speaker") == "Voice A" for t in focus)

    # Retrieval seeds must contain explicit topic
    seeds = recorded.retrieval_seeds()
    assert "vector database" in seeds
