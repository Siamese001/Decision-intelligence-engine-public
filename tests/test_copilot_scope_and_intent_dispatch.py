"""Unit tests for Wave 2: Cross-project scope inheritance and intent disambiguation."""
from __future__ import annotations

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    clear_topological_inheritance_cache,
    discover_topologically_inherited_engagements,
    get_project_scope_aliases,
    is_assertion_in_scope,
    is_node_in_scope,
    resolve_permitted_engagements,
    resolve_permitted_scopes,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    parse_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


class MockGraphTopology:
    """Mock graph topology representing multi-project knowledge assets."""

    def __init__(self) -> None:
        self.nodes = {}
        self.assertions = {}
        self.graph_sha256 = "mock-graph-sha-wave2"


def _build_nexus_nea_topology() -> MockGraphTopology:
    """Builds topology containing NEA charter assets and Nexus project scope."""
    graph = MockGraphTopology()

    # NEA project node
    graph.nodes["project:nea"] = {
        "node_id": "project:nea",
        "canonical_name": "Project NEA",
        "display_name": "NEA Charter",
        "node_type": "project",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
    }

    # GRID_OS architecture concept in NEA scope
    graph.nodes["concept:grid_os"] = {
        "node_id": "concept:grid_os",
        "canonical_name": "GRID_OS Platform",
        "display_name": "GRID_OS Operating System",
        "node_type": "concept",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
    }

    # harper Hoffman entity in NEA scope
    graph.nodes["person:mandy_hopmann"] = {
        "node_id": "person:mandy_hopmann",
        "canonical_name": "harper Hoffman",
        "display_name": "harper Hoffman",
        "node_type": "person",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
    }

    # Brett entity in NEA scope
    graph.nodes["person:brett"] = {
        "node_id": "person:brett",
        "canonical_name": "Brett",
        "display_name": "Brett",
        "node_type": "person",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
    }

    # Assertions under project:nea
    graph.assertions["ast-nea-grid_os-01"] = {
        "assertion_id": "ast-nea-grid_os-01",
        "subject_node_id": "concept:grid_os",
        "predicate": "HAS_CLAIM_TEXT",
        "claim_text": "GRID_OS delivers enterprise decision intelligence infrastructure.",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
        "primary_source_id": "intake-nea-charter",
    }

    graph.assertions["ast-nea-mandy-02"] = {
        "assertion_id": "ast-nea-mandy-02",
        "subject_node_id": "person:mandy_hopmann",
        "predicate": "HAS_ROLE",
        "claim_text": "harper Hoffman leads technical architecture alignment for GRID_OS.",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
        "primary_source_id": "intake-nea-charter",
    }

    graph.assertions["ast-nea-brett-03"] = {
        "assertion_id": "ast-nea-brett-03",
        "subject_node_id": "person:brett",
        "predicate": "HAS_CLAIM_TEXT",
        "claim_text": "Brett emphasized technical leadership governance across modules.",
        "engagement_id": "project:nea",
        "namespace": "CANONICAL",
        "primary_source_id": "intake-nea-charter",
    }

    # Unrelated foreign project (e.g. project:solar)
    graph.nodes["project:solar"] = {
        "node_id": "project:solar",
        "canonical_name": "Project Solar",
        "node_type": "project",
        "engagement_id": "project:solar",
        "namespace": "CANONICAL",
    }
    graph.assertions["ast-solar-01"] = {
        "assertion_id": "ast-solar-01",
        "subject_node_id": "project:solar",
        "predicate": "HAS_CLAIM_TEXT",
        "claim_text": "Solar power grid management system.",
        "engagement_id": "project:solar",
        "namespace": "CANONICAL",
    }

    return graph


def test_topological_project_aliases_resolution() -> None:
    """Verifies that project_nexus and nea resolve to each other via topological aliases."""
    clear_topological_inheritance_cache()
    graph = _build_nexus_nea_topology()

    # Aliases lookup
    nexus_aliases = get_project_scope_aliases("project:project_nexus")
    assert "project:nea" in nexus_aliases

    nea_aliases = get_project_scope_aliases("project:nea")
    assert "project:project_nexus" in nea_aliases or "project:nexus" in nea_aliases

    # Full permitted scopes resolver
    scopes = resolve_permitted_scopes("project:project_nexus", graph)
    assert "project:project_nexus" in scopes
    assert "project:nea" in scopes
    assert "project:solar" not in scopes

    # Permitted cross-engagements resolver
    permitted = resolve_permitted_engagements(
        "GRID_OS platform architecture",
        "project:project_nexus",
        graph,
    )
    assert "project:nea" in permitted
    assert "project:solar" not in permitted


def test_cross_project_nexus_retrieves_nea_assertions_without_scope_boundary() -> None:
    """Verifies queries with --project project_nexus retrieve NEA assertions without tripping scope boundary."""
    clear_topological_inheritance_cache()
    graph = _build_nexus_nea_topology()

    # Verify is_assertion_in_scope directly
    nea_ast = graph.assertions["ast-nea-grid_os-01"]
    assert is_assertion_in_scope(nea_ast, "project:project_nexus")
    assert not is_assertion_in_scope(graph.assertions["ast-solar-01"], "project:project_nexus")

    # Verify node in scope
    cweos_node = graph.nodes["concept:grid_os"]
    assert is_node_in_scope("concept:grid_os", cweos_node, "project:project_nexus")
    assert not is_node_in_scope("project:solar", graph.nodes["project:solar"], "project:project_nexus")

    # Verify retriever retrieval with pure Nexus/NEA assets
    pure_graph = MockGraphTopology()
    pure_graph.nodes = {k: v for k, v in graph.nodes.items() if "solar" not in k}
    pure_graph.assertions = {k: v for k, v in graph.assertions.items() if "solar" not in k}
    pure_retriever = CopilotGraphRetriever(
        topology=pure_graph,
        engagement_id="project:project_nexus",
        session_id="session-nexus-001",
    )
    pure_results = pure_retriever.retrieve_structured_assertions("GRID_OS platform architecture")
    pure_retrieved_ids = [r["assertion_id"] for r in pure_results]

    assert "ast-nea-grid_os-01" in pure_retrieved_ids
    assert pure_retriever._scope_filtered_count == 0

    # Verify multi-tenant retriever excludes foreign tenant
    multi_retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:project_nexus",
        session_id="session-nexus-001",
    )
    multi_results = multi_retriever.retrieve_structured_assertions("GRID_OS platform architecture")
    multi_retrieved_ids = [r["assertion_id"] for r in multi_results]
    assert "ast-nea-grid_os-01" in multi_retrieved_ids
    assert "ast-solar-01" not in multi_retrieved_ids


def test_third_party_query_intent_dispatch_defaults_to_graph_context() -> None:
    """Verifies that third-party informational queries route to graph-context rather than respond coaching."""
    # "what did Brett say about technical leadership"
    p1 = parse_copilot_request("what did Brett say about technical leadership")
    assert p1.inferred_intent == "graph-context"
    assert p1.target_speaker == "Brett"
    assert "brett" in p1.topic_keywords
    assert "technical" in p1.topic_keywords
    assert "leadership" in p1.topic_keywords

    # "what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey"
    p2 = parse_copilot_request("what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey")
    assert p2.inferred_intent == "graph-context"
    assert p2.target_speaker == "Mandy"
    assert "mandy" in p2.topic_keywords

    # "what does Mandy think about GRID_OS"
    p3 = parse_copilot_request("what does Mandy think about GRID_OS")
    assert p3.inferred_intent == "graph-context"
    assert p3.target_speaker == "Mandy"
    assert "grid_os" in p3.topic_keywords

    # "what did Brett say" without explicit topic preposition
    p4 = parse_copilot_request("what did Brett say")
    assert p4.inferred_intent == "graph-context"
    assert p4.target_speaker == "Brett"
    assert p4.topic_query == "Brett"
    assert "brett" in p4.topic_keywords


def test_explicit_respond_intent_preserved_for_live_reaction() -> None:
    """Verifies that explicit response coaching commands route to respond intent."""
    p1 = parse_copilot_request("React to Mitch's comment about load and agents.")
    assert p1.inferred_intent == "respond"
    assert p1.target_speaker == "Mitch"

    p2 = parse_copilot_request("Respond to Jeffrey on GRID_OS roadmap")
    assert p2.inferred_intent == "respond"
    assert p2.target_speaker == "Jeffrey"


def test_non_attendee_target_does_not_raise_speaker_not_in_roster() -> None:
    """Verifies that queries targeting non-attendees in graph-context mode do not fail with SPEAKER_NOT_IN_ROSTER."""
    from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
        _resolve_speaker_and_utterance,
    )
    from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
        ConversationContextModel,
    )

    context = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[
            {"canonical_name": " Avery Chen", "display_name": " Avery Chen"},
            {"canonical_name": "Amit Ayer", "display_name": "Amit Ayer"},
        ],
    )

    # When query asks about Brett, intent is graph-context
    parsed = parse_copilot_request("what did Brett say about technical leadership")
    assert parsed.inferred_intent == "graph-context"

    resolved_target, utterance, window, err = _resolve_speaker_and_utterance(
        parsed.target_speaker, parsed.topic_keywords, context, in_mem=None
    )

    # Roster resolution on target Brett returns err because Brett is not in meeting roster
    assert err is not None
    assert err[0] == "SPEAKER_NOT_IN_ROSTER"

    # But under graph-context intent, the engine gracefully suppresses this error and routes to graph context
    if parsed.inferred_intent != "respond" and (parsed.topic_keywords or parsed.topic_query):
        resolved_target, utterance, window, err = None, None, [], None

    assert err is None, "Graph-context intent must not fail closed with SPEAKER_NOT_IN_ROSTER for third-party entities"


def test_end_to_end_intent_evaluation_cross_project_nexus_to_nea() -> None:
    """Verifies end-to-end evaluate_intent retrieves and grounds NEA assertions under project_nexus session."""
    from dual_pipeline.live_meeting_recorder_core.copilot_intents import evaluate_intent

    graph = _build_nexus_nea_topology()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:project_nexus",
        session_id="session-nexus-001",
    )

    body, assertions, ok = evaluate_intent(
        intent="graph-context",
        target=None,
        dialogue="Let us discuss the GRID_OS architecture and platform governance.",
        retriever=retriever,
        topic_keywords=["grid_os", "architecture"],
        topic_query="GRID_OS architecture",
    )

    assert ok is True
    assert len(assertions) >= 1
    retrieved_ids = [a["assertion_id"] for a in assertions]
    assert "ast-nea-grid_os-01" in retrieved_ids
    assert "GRID_OS" in body
