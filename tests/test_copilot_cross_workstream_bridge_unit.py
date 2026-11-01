"""
tests/test_copilot_cross_workstream_bridge_unit.py

Wave 3 unit tests:
1. Cross-workstream entity resolution (harper Hoffman -> Conduit / LENS) under project:project_nexus session.
2. Graph-wide alias registry matching (Mandy Bambenek Hopmann -> stakeholder_mandy_hopmann).
3. Explicit workstream attribution prefixing ([PD Conduit / LENS]) in spoken copilot delivery.
4. Prevention of generic token fallback to unrelated governance claims when named cross-workstream entities match.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.cli.copilot_speech import render_speaker_comments
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_intents import (
    eval_graph_context_with_topic,
    eval_topic_respond,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


@pytest.fixture(scope="module")
def active_retriever() -> CopilotGraphRetriever:
    repo_root = Path(__file__).resolve().parent.parent
    graph_path = (
        repo_root
        / ".local"
        / "dual_pipeline"
        / "generations"
        / "generation-4294649f027622625be85a9ec510"
        / "graph.sqlite"
    )
    if not graph_path.is_file():
        pytest.skip(f"Active graph not found at {graph_path}")
    topology = InMemoryGraphTopology.load_from_sqlite(graph_path)
    return CopilotGraphRetriever(
        topology=topology,
        engagement_id="project:project_nexus",
    )


def test_mandy_hopmann_conduit_retrieval_under_nexus_session(active_retriever: CopilotGraphRetriever):
    """Verifies harper Hoffman and Conduit retrieve accurately during a project_nexus session."""
    body, asrts, ok = eval_graph_context_with_topic(
        active_retriever,
        topic_keywords=["Mandy", "Hopmann", "Conduit"],
        topic_query="harper Hoffman Conduit",
    )

    assert ok is True
    assert len(asrts) > 0

    cited_ids = {a.get("assertion_id") for a in asrts}
    target_ids = {
        "asrt_claim_claim_v44_roster_row_2",
        "assertion-adf470c7d9f6766a029189884359",
        "assertion-8b7dc2f81c832332d3eeb25c8cbb",
    }
    matched_ids = cited_ids & target_ids
    assert len(matched_ids) >= 1, (
        f"Expected at least one Conduit/Mandy target assertion in {cited_ids}"
    )

    full_text = body.lower()
    assert "conduit" in full_text
    assert "harper Hoffman" in full_text


def test_eval_topic_respond_with_alias_expansion(active_retriever: CopilotGraphRetriever):
    """Verifies alias expansion matches LinkedIn alias Mandy Bambenek Hopmann -> stakeholder_mandy_hopmann."""
    body, asrts, ok = eval_topic_respond(
        active_retriever,
        target="Mandy Bambenek Hopmann",
        selected_utterance=None,
        topic_keywords=["Conduit"],
        topic_query="Conduit",
    )

    assert ok is True
    assert len(asrts) > 0

    cited_ids = {a.get("assertion_id") for a in asrts}
    assert (
        "asrt_claim_claim_v44_roster_row_2" in cited_ids
        or "assertion-adf470c7d9f6766a029189884359" in cited_ids
        or "assertion-8b7dc2f81c832332d3eeb25c8cbb" in cited_ids
    )


def test_copilot_speech_workstream_provenance_prefix(active_retriever: CopilotGraphRetriever):
    """Verifies that cross-project assertions receive explicit organizational provenance prefix."""
    _, asrts, ok = eval_topic_respond(
        active_retriever,
        target="harper Hoffman",
        selected_utterance=None,
        topic_keywords=["Conduit"],
        topic_query="Conduit",
    )
    assert ok is True
    assert len(asrts) > 0

    res = render_speaker_comments(
        intent="respond",
        target="harper Hoffman",
        assertions=asrts,
        max_words=120,
        retriever=active_retriever,
    )

    assert res.rendered_count > 0
    assert "[PD Conduit" in res.body
    # Verify harper Hoffman's role or Conduit scope is spoken
    lower_body = res.body.lower()
    assert "conduit" in lower_body
    assert "harper Hoffman" in lower_body


def test_no_unrelated_governance_fallback(active_retriever: CopilotGraphRetriever):
    """Verifies generic token fallback does not inject unrelated governance claims."""
    _, asrts, ok = eval_graph_context_with_topic(
        active_retriever,
        topic_keywords=["Mandy", "Hopmann", "Conduit"],
        topic_query="harper Hoffman Conduit",
    )
    assert ok is True
    for a in asrts:
        claim_str = str(a.get("claim_text") or a.get("object_literal") or "").lower()
        pred_str = str(a.get("predicate") or "").lower()
        # Verify no unrelated Chakra protocol governance rules pollute the cross-workstream context
        assert "protocol_decision" not in claim_str
        assert "chakra_gov" not in claim_str
