"""
tests/test_copilot_dialogue_merging_e2e.py

End-to-End verification that Live Copilot intent evaluation blends live dialogue
context model turns and retrieval seeds with explicit operator topics and targets.
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
    eval_graph_context_with_topic,
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


class MockRetriever(CopilotGraphRetriever):
    """Hermetic test retriever recording exact query inputs."""

    def __init__(self, topology: InMemoryGraphTopology) -> None:
        super().__init__(topology=topology, engagement_id="project:project_nexus")
        self.last_query: Any = None
        self.recorded_assertions = [
            {
                "assertion_id": "ast-test-1",
                "predicate": "OPEN_QUESTION",
                "claim_text": "Vector database persistent storage scaling requires partition bounds",
                "is_priority": True,
                "is_primary": True,
                "subject_node_id": "system:vector_store",
                "provenance_scope": "PRIMARY",
            }
        ]

    def get_next_questions(self, dialogue: Any, max_questions: int = 3) -> List[Dict[str, Any]]:
        self.last_query = dialogue
        return self.recorded_assertions[:max_questions]

    def get_next_actions(self, dialogue: Any, max_actions: int = 3) -> List[Dict[str, Any]]:
        self.last_query = dialogue
        return self.recorded_assertions[:max_actions]

    def retrieve_structured_assertions(self, query: Any, max_results: int = 10, priority_only: bool = False) -> List[Dict[str, Any]]:
        self.last_query = query
        return self.recorded_assertions[:max_results]


def test_copilot_eval_questions_blends_dialogue_and_topic(
    mock_topology: InMemoryGraphTopology,
):
    """Verifies eval_questions retains both live dialogue seeds and explicit topic query."""
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[{"name": "Amit Ayer", "role": "Lead Architect"}],
    )
    context_model.add_turn(
        speaker="Voice A",
        text="And you can always set up a Vector database.",
        timestamp="07:40",
        turn_sec=460.0,
    )
    context_model.add_turn(
        speaker="Voice B",
        text="What are the latency constraints on retrieval?",
        timestamp="07:50",
        turn_sec=470.0,
    )

    retriever = MockRetriever(mock_topology)

    body, assertions, ok = eval_questions(
        retriever=retriever,
        dialogue=context_model,
        topic_query="vector database",
        topic_keywords=["vector", "database"],
        target=None,
    )

    assert ok is True
    assert len(assertions) >= 1
    assert "Recommended Questions:" in body

    recorded_query = retriever.last_query
    assert isinstance(recorded_query, BlendedDialogueContext)
    seeds = recorded_query.retrieval_seeds()
    assert "vector database" in seeds
    assert "vector" in seeds
    assert "database" in seeds

    focus = recorded_query.focus_turns()
    assert len(focus) >= 2
    assert any("Vector database" in t.get("text", "") for t in focus)


def test_copilot_eval_actions_blends_dialogue_and_topic(
    mock_topology: InMemoryGraphTopology,
):
    """Verifies eval_actions retains live dialogue context model seeds."""
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[{"name": "Amit Ayer", "role": "Lead Architect"}],
    )
    context_model.add_turn(
        speaker="Voice A",
        text="We should benchmark vector ingestion pipelines.",
        timestamp="08:00",
        turn_sec=480.0,
    )

    retriever = MockRetriever(mock_topology)

    body, assertions, ok = eval_actions(
        retriever=retriever,
        dialogue=context_model,
        topic_query="vector ingestion",
        topic_keywords=["vector", "ingestion"],
        target="Amit",
    )

    assert ok is True
    assert len(assertions) >= 1
    assert "Recommended Actions:" in body

    recorded_query = retriever.last_query
    assert isinstance(recorded_query, BlendedDialogueContext)
    seeds = recorded_query.retrieval_seeds()
    assert "Amit" in seeds
    assert "vector ingestion" in seeds


def test_copilot_eval_graph_context_with_topic_retains_dialogue(
    mock_topology: InMemoryGraphTopology,
):
    """Verifies eval_graph_context_with_topic blends dialogue seeds into entity resolution."""
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[{"name": "Daniel Brankin", "role": "Product Owner"}],
    )
    context_model.add_turn(
        speaker="Daniel",
        text="Discussing persistent storage infrastructure.",
        timestamp="08:15",
        turn_sec=495.0,
    )

    retriever = MockRetriever(mock_topology)

    body, assertions, ok = eval_graph_context_with_topic(
        retriever=retriever,
        topic_keywords=["storage"],
        topic_query="persistent storage",
        dialogue=context_model,
    )

    assert ok is True
    assert len(assertions) >= 1
    assert "Verified Graph Knowledge:" in body
    assert isinstance(retriever.last_query, BlendedDialogueContext)
