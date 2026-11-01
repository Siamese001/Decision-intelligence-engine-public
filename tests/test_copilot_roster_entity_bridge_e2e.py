"""
tests/test_copilot_roster_entity_bridge_e2e.py

End-to-end verification of attendee roster bridging, domain role synonym expansion,
and hybrid graph retrieval for executive copilot queries.
Verifies the exact incident query: "SAP tech owner jason".
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    filter_assertions_for_utterance_topic,
    parse_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_intents import (
    eval_graph_context_with_topic,
    eval_questions,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import (
    load_session_attendees,
    parse_attendees_from_metadata,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def active_graph_topology() -> InMemoryGraphTopology:
    """Loads active graph topology from local generations directory."""
    pair_path = REPO_ROOT / ".local" / "dual_pipeline" / "active_pair.json"
    if pair_path.is_file():
        with open(pair_path, "r", encoding="utf-8") as f:
            pair = json.load(f)
        graph_rel = pair.get("graph_path")
        if graph_rel:
            full_path = pair_path.parent / graph_rel
            if full_path.is_file():
                return InMemoryGraphTopology.load_from_sqlite(full_path)
    # Fallback to direct generation path
    gen_db = REPO_ROOT / ".local" / "dual_pipeline" / "generations" / "generation-9b0ac81b16d44cb65d410da618b7" / "graph.sqlite"
    return InMemoryGraphTopology.load_from_sqlite(gen_db)


@pytest.fixture
def jarvis_meeting_attendees() -> list[dict[str, str]]:
    """Loads confirmed attendees from the authentic Jarvis Kickoff metadata."""
    meta_path = REPO_ROOT / "output" / "project_nexus" / "10-07-2026" / "Jarvis_Kickoff_Jill_PartnerAdvisory" / "metadata.json"
    if meta_path.is_file():
        attendees = load_session_attendees(meta_path.parent)
        if attendees:
            return attendees
    return [
        {"name": "Daniel Brankin", "role": "Project Development Lead / Jarvis Product Owner", "org": "Apex Global Enterprises"},
        {"name": "Jill Cizewski", "role": "Business Lead / Project Development Specialist", "org": "Apex Global Enterprises"},
        {"name": "Taylor Mercer", "role": "Delivery Lead / PM", "org": "PartnerAdvisory"},
        {"name": "Mitch Garstka", "role": "Technical Lead", "org": "PartnerAdvisory"},
        {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
        {"name": "Jason Lickliter", "role": "Full Stack SWE - contractor", "org": "Apex Global Enterprises"},
    ]


def test_copilot_roster_entity_bridge_sap_tech_owner_jason_retrieval(
    active_graph_topology: InMemoryGraphTopology,
    jarvis_meeting_attendees: list[dict[str, str]],
):
    """
    E2E Test: Query 'SAP tech owner jason' retrieves attendee Jason Lickliter (Full Stack SWE)
    and SAP technical anchor assertions.
    """
    retriever = CopilotGraphRetriever(
        topology=active_graph_topology,
        attendees=jarvis_meeting_attendees,
        engagement_id="project:project_nexus",
    )

    # Ingress parsing
    parsed = parse_copilot_request("-copilot SAP tech owner jason question")
    assert parsed.target_speaker == "Jason"
    assert parsed.topic_query is not None
    assert "sap" in parsed.topic_keywords
    assert "tech" in parsed.topic_keywords
    assert "owner" in parsed.topic_keywords

    # Structured retrieval
    query_str = f"{parsed.target_speaker} {parsed.topic_query}"
    candidates = retriever.retrieve_structured_assertions(query_str, max_results=15)
    assert len(candidates) > 0

    # Verify attendee presence
    attendee_candidates = [
        c for c in candidates
        if "jason" in str(c.get("claim_text", "")).lower() or "jason" in str(c.get("subject_node_id", "")).lower()
    ]
    assert len(attendee_candidates) >= 1
    assert any("full stack swe" in str(c.get("claim_text", "")).lower() for c in attendee_candidates)

    # Verify technology anchor presence
    sap_candidates = [
        c for c in candidates
        if "sap" in str(c.get("claim_text", "")).lower() or "sap" in str(c.get("subject_node_id", "")).lower()
    ]
    assert len(sap_candidates) >= 1


def test_copilot_roster_bridge_intent_evaluation_questions(
    active_graph_topology: InMemoryGraphTopology,
    jarvis_meeting_attendees: list[dict[str, str]],
):
    """Verifies eval_questions produces grounded questions for the incident prompt."""
    retriever = CopilotGraphRetriever(
        topology=active_graph_topology,
        attendees=jarvis_meeting_attendees,
        engagement_id="project:project_nexus",
    )

    parsed = parse_copilot_request("-copilot SAP tech owner jason question")
    body, assertions, ok = eval_questions(
        retriever=retriever,
        dialogue=None,
        topic_query=parsed.topic_query,
        topic_keywords=parsed.topic_keywords,
        target=parsed.target_speaker,
    )

    assert ok is True
    assert len(assertions) >= 1
    assert "Recommended Questions:" in body


def test_copilot_roster_bridge_graph_context_with_topic(
    active_graph_topology: InMemoryGraphTopology,
    jarvis_meeting_attendees: list[dict[str, str]],
):
    """Verifies eval_graph_context_with_topic resolves grounded candidates with domain synonyms."""
    retriever = CopilotGraphRetriever(
        topology=active_graph_topology,
        attendees=jarvis_meeting_attendees,
        engagement_id="project:project_nexus",
    )

    parsed = parse_copilot_request("SAP tech owner")
    body, assertions, ok = eval_graph_context_with_topic(
        retriever=retriever,
        topic_keywords=parsed.topic_keywords,
        topic_query=parsed.topic_query,
    )

    assert ok is True
    assert len(assertions) >= 1
    assert "Verified Graph Knowledge:" in body
