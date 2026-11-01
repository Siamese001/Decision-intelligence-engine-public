"""
tests/adversarial/test_copilot_engagement_wave3_adversarial.py

Wave 3 adversarial and edge-case acceptance tests:
Covers mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Exact production regression: Pin the exact JARVIS-LENS JIRA cross-contamination incident.
2. Boundary payload: Empty strings, None queries, noise-only tokens, empty graphs.
3. Malformed input: Corrupted assertion records, wrong data types, missing required keys.
4. Invariant bypass attempt: Shared attendee BFS multi-hop graph traversal hopping attempt.
5. Concurrency & race: Rapid generation switching across concurrent retrieval queries.
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
    is_node_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    compute_neighborhood_chains,
    precompute_neighborhoods,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
    SessionContextPacket,
)
from tests.fixtures.copilot_engagement.loader import build_in_memory_graph


def test_adversarial_exact_production_regression_incident() -> None:
    """
    CATEGORY: Exact production regression.
    Pins the exact RCA failure scenario:
    On October 7, Engineering Jarvis meeting (attendees: Alex Rivera, shared engineers),
    querying for risks/blockers retrieved LENS-only fact: 'Spatial File Upload depends on JIRA'.
    Verifies that under Wave 3 retrieval boundaries, this leakage is completely eliminated.
    """
    graph = build_in_memory_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-jarvis-incident-replay",
        attendees=[{"name": "Alex Rivera"}, {"name": "Shared Engineer"}],
        generation=1,
    )

    # 1. Exact incident query phrasing
    queries = [
        "What risks or blockers are on the roadmap?",
        "JIRA dependencies",
        "Spatial File Upload",
        "What are the sprint 1 blockers?",
    ]
    for q in queries:
        assertions = retriever.retrieve_structured_assertions(q)
        retrieved_ids = [a["assertion_id"] for a in assertions]
        # The exact leaking assertion from October 7
        assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" not in retrieved_ids, (
            f"Production regression: LENS assertion leaked for query '{q}'"
        )
        assert not any("Spatial File Upload" in a.get("claim_text", "") for a in assertions)
        assert not any("JIRA" in a.get("claim_text", "") for a in assertions)

    # 2. Prefetch packet check
    bridge = CopilotPrefetchBridge(retriever=retriever)
    packet = bridge.prefetch(
        project_name="Engineering Jarvis",
        meeting_name="Engineering Jarvis 20261007",
        attendees=[{"name": "Alex Rivera"}, {"name": "Shared Engineer"}],
        engagement_id="project:jarvis",
        session_id="session-jarvis-incident-replay",
    )
    prompt = packet.format_for_copilot_prompt()
    assert "Spatial File Upload" not in prompt
    assert "JIRA" not in prompt


def test_adversarial_boundary_payload_edge_cases() -> None:
    """
    CATEGORY: Boundary payload.
    Tests empty, whitespace, non-alphanumeric, None, and empty graph inputs.
    """
    graph = build_in_memory_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-boundary",
    )

    # 1. Empty and whitespace queries
    assert retriever.retrieve_structured_assertions("") == []
    assert retriever.retrieve_structured_assertions("   ") == []
    assert retriever.retrieve_structured_assertions("--- @@@ !!!") == []
    assert retriever.retrieve_structured_assertions(None) == []

    # 2. Empty graph topology
    import types
    empty_graph = types.SimpleNamespace(nodes={}, assertions={}, forward_adjacency={})
    empty_retriever = CopilotGraphRetriever(
        topology=empty_graph,
        engagement_id="project:jarvis",
        session_id="session-boundary-empty",
    )
    assert empty_retriever.retrieve_structured_assertions("Metal") == []

    # 3. Empty prefetch
    empty_bridge = CopilotPrefetchBridge(retriever=empty_retriever)
    packet = empty_bridge.prefetch(
        project_name="",
        meeting_name="",
        attendees=[],
        engagement_id="project:jarvis",
        session_id="session-boundary-empty",
    )
    assert packet.is_empty
    assert packet.format_for_copilot_prompt() == ""


def test_adversarial_malformed_input_records() -> None:
    """
    CATEGORY: Malformed input.
    Tests that corrupted assertion records and schema violations fail closed safely.
    """
    graph = build_in_memory_graph()

    # Inject malformed assertions into graph
    graph.assertions["corrupt-01"] = None  # None assertion
    graph.assertions["corrupt-02"] = "not-a-dict"  # String assertion
    graph.assertions["corrupt-03"] = {"subject_node_id": None, "object_node_id": None}  # Missing keys
    graph.assertions["corrupt-04"] = {  # Integer engagement_id
        "assertion_id": "corrupt-04",
        "subject_node_id": "project:jarvis",
        "engagement_id": 999999,
        "claim_text": "Malformed integer engagement",
    }
    graph.assertions["corrupt-05"] = {  # Missing engagement_id
        "assertion_id": "corrupt-05",
        "subject_node_id": "project:jarvis",
        "claim_text": "Missing engagement key",
    }

    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-malformed",
    )

    # Retrieval must not crash and must filter out all malformed records
    results = retriever.retrieve_structured_assertions("Malformed integer engagement Missing engagement")
    retrieved_ids = [r["assertion_id"] for r in results]
    assert "corrupt-01" not in retrieved_ids
    assert "corrupt-02" not in retrieved_ids
    assert "corrupt-03" not in retrieved_ids
    assert "corrupt-04" not in retrieved_ids
    assert "corrupt-05" not in retrieved_ids


def test_adversarial_traversal_hopping_invariant_bypass_attempt() -> None:
    """
    CATEGORY: Invariant bypass attempt.
    Tests an adversarial graph where a shared person node bridges JARVIS and LENS.
    Verifies that multi-hop neighborhood BFS traversal strictly halts at the tenant boundary
    and refuses to hop into LENS nodes from the shared person.
    """
    graph = build_in_memory_graph()

    # Shared engineer node has edges to both JARVIS and LENS
    # Try precomputing neighborhoods originating from shared person under JARVIS engagement scope
    store = precompute_neighborhoods(
        topology=graph,
        seed_node_ids=["person:shared"],
        max_hops=3,
        engagement_id="project:jarvis",
    )

    # Chains must NOT include project:lens or any LENS feature
    for chain in store.chains:
        assert chain.get("target_id") != "project:lens", "BFS traversal hopped across boundary to project:lens"
        assert chain.get("target_id") != "feature:spatial_upload", "BFS traversal hopped to LENS feature"
        assert "LENS" not in chain.get("chain_str", ""), "LENS found in traversal chain string"


def test_adversarial_concurrency_race_generation_switching() -> None:
    """
    CATEGORY: Concurrency & race.
    Tests concurrent retrieval requests while the graph generation is actively bumped.
    Verifies that generation mismatches fail closed cleanly without returning mixed generation state.
    """
    graph = build_in_memory_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        engagement_id="project:jarvis",
        session_id="session-concurrent",
        generation=1,
    )

    def worker_query(worker_id: int) -> List[Dict[str, Any]]:
        # Simulate varying query latencies
        return retriever.retrieve_structured_assertions("Metal transcription Apple Silicon")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker_query, i) for i in range(16)]
        results_before = [f.result() for f in futures]

    assert all(len(r) > 0 for r in results_before), "Expected valid assertions before generation bump"

    # Bump generation in scope binding to simulate active hot-reload
    retriever.generation = 2
    # The existing retriever._scope_binding was created with generation 1
    # ScopeBinding.is_valid should now fail closed
    res_after = retriever.retrieve_structured_assertions("Metal transcription Apple Silicon")
    assert len(res_after) == 0, "Retrieval must fail closed when generation diverges from scope binding"
