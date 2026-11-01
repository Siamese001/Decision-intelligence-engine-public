"""
tests/test_copilot_error_telemetry_e2e.py

End-to-end qualification of precision error telemetry and anti-masking diagnostic messages.
Verifies the four distinct error codes and actionable user messages:
- SCOPE_FILTERED -> [Scope Boundary] Assertions exist but were filtered by active project scope
- ENTITY_NOT_FOUND -> [Entity Resolution] Target '<entity>' not found in graph or active roster
- PARSER_NO_TOPIC -> [Prompt Parser] No recognizable topic or attendee extracted from prompt
- ZERO_MATCHING_ASSERTIONS -> [Graph Retrieval] No relevant graph assertions match the current discussion
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_GRAPH_BINDING,
    CATEGORY_SCOPE_BOUNDARY,
    CATEGORY_USAGE,
    categorize_copilot_terminal_reason,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _copilot_failure,
    _diagnose_unresolved_evidence,
    _evaluate_and_render_copilot_response,
    _is_entity_present,
    _parse_and_validate_copilot_args,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


from types import SimpleNamespace


@pytest.fixture
def mock_graph_topology() -> Any:
    return SimpleNamespace(
        nodes={
            "tech:sap": {
                "canonical_name": "SAP",
                "display_name": "SAP ERP",
                "node_type": "technology",
                "engagement_id": "project:project_nexus",
            },
            "tech:maximo": {
                "canonical_name": "Maximo",
                "display_name": "IBM Maximo",
                "node_type": "technology",
                "engagement_id": "project:other_project",
            },
        },
        assertions={
            "asrt_sap_nexus": {
                "assertion_id": "asrt_sap_nexus",
                "subject_node_id": "tech:sap",
                "predicate": "INTEGRATES_WITH",
                "object_node_id": "tech:maximo",
                "claim_text": "SAP integrates with IBM Maximo for asset procurement.",
                "engagement_id": "project:project_nexus",
                "canonical_status": "ACTIVE",
            },
        },
        forward_adjacency={},
    )


def test_scope_filtered_error_telemetry(mock_graph_topology: InMemoryGraphTopology) -> None:
    """When scope filtering excludes assertions, emitted message explicitly indicates [Scope Boundary]."""
    # Initialize retriever scoped to a foreign engagement that excludes project:project_nexus
    retriever = CopilotGraphRetriever(
        topology=mock_graph_topology,
        engagement_id="project:isolated_tenant",
        generation=1,
    )
    # The retriever should have filtered out the SAP assertion due to scope
    assert retriever._scope_filtered_count > 0

    code, det = _diagnose_unresolved_evidence(
        retriever,
        topic_keywords=["sap"],
        topic_query="SAP procurement",
        target=None,
    )
    assert code == "SCOPE_FILTERED"
    assert "Assertions exist but were filtered by active project scope" in det

    cat, user_msg = categorize_copilot_terminal_reason(code, det)
    assert cat == CATEGORY_SCOPE_BOUNDARY
    assert user_msg.startswith("[Scope Boundary]")
    assert "Assertions exist but were filtered by active project scope" in user_msg


def test_entity_not_found_error_telemetry(mock_graph_topology: InMemoryGraphTopology) -> None:
    """When target entity is absent, message indicates [Entity Resolution] Target '<entity>' not found."""
    retriever = CopilotGraphRetriever(
        topology=mock_graph_topology,
        engagement_id="project:project_nexus",
        generation=1,
    )
    # Ensure helper detects missing entity
    assert not _is_entity_present("GhostPerson", retriever)

    code, det = _diagnose_unresolved_evidence(
        retriever,
        topic_keywords=["sap"],
        topic_query="SAP architecture",
        target="GhostPerson",
    )
    assert code == "ENTITY_NOT_FOUND"
    assert det == "Target 'GhostPerson' not found in graph or active roster."

    cat, user_msg = categorize_copilot_terminal_reason(code, det)
    assert cat == CATEGORY_ABSENT_CANDIDATES
    assert user_msg == "[Entity Resolution] Target 'GhostPerson' not found in graph or active roster."


def test_parser_no_topic_error_telemetry(mock_graph_topology: InMemoryGraphTopology) -> None:
    """When parser finds no topic, message indicates [Prompt Parser] No recognizable topic or attendee."""
    retriever = CopilotGraphRetriever(
        topology=mock_graph_topology,
        engagement_id="project:project_nexus",
        generation=1,
    )

    code, det = _diagnose_unresolved_evidence(
        retriever,
        topic_keywords=[],
        topic_query="",
        target=None,
    )
    assert code == "PARSER_NO_TOPIC"
    assert det == "No recognizable topic or attendee extracted from prompt."

    cat, user_msg = categorize_copilot_terminal_reason(code, det)
    assert cat == CATEGORY_USAGE
    assert user_msg == "[Prompt Parser] No recognizable topic or attendee extracted from prompt."


def test_parser_no_topic_cli_argument_validation() -> None:
    """CLI argument parser validates unparseable prompt and fails closed with PARSER_NO_TOPIC."""
    args = argparse.Namespace(
        request="???",
        intent=None,
        target=None,
        topic=None,
        json=True,
        runtime_root=None,
    )
    req_tuple, fail_res = _parse_and_validate_copilot_args(args)
    assert req_tuple is None
    assert fail_res is not None
    assert fail_res["error"] == "PARSER_NO_TOPIC"
    assert "[Prompt Parser]" in fail_res["user_message"]
    assert "No recognizable topic or attendee extracted from prompt" in fail_res["user_message"]


def test_zero_matching_assertions_error_telemetry(mock_graph_topology: InMemoryGraphTopology) -> None:
    """When entities/topics are found but no assertions connect them, emits [Graph Retrieval]."""
    retriever = CopilotGraphRetriever(
        topology=mock_graph_topology,
        engagement_id="project:project_nexus",
        generation=1,
    )

    # Query a topic known to have nodes in topology but no assertions connecting it to discussion
    code, det = _diagnose_unresolved_evidence(
        retriever,
        topic_keywords=["unconnected_topic"],
        topic_query="unconnected_topic query",
        target=None,
    )
    assert code == "ZERO_MATCHING_ASSERTIONS"
    assert det == "No relevant graph assertions match the current discussion."

    cat, user_msg = categorize_copilot_terminal_reason(code, det)
    assert cat == CATEGORY_ABSENT_CANDIDATES
    assert user_msg == "[Graph Retrieval] No relevant graph assertions match the current discussion."


def test_attempt_ledger_persistence_for_granular_errors(mock_graph_topology: InMemoryGraphTopology) -> None:
    """Verifies that all failure modes persist truthful, granular records to copilot_attempts.jsonl."""
    with tempfile.TemporaryDirectory(prefix="copilot_telemetry_test_") as tmp_dir:
        out_dir = Path(tmp_dir)

        test_cases = [
            ("SCOPE_FILTERED", "Assertions exist but were filtered by active project scope.", "[Scope Boundary]"),
            ("ENTITY_NOT_FOUND", "Target 'Tenika' not found in graph or active roster.", "[Entity Resolution]"),
            ("PARSER_NO_TOPIC", "No recognizable topic or attendee extracted from prompt.", "[Prompt Parser]"),
            ("ZERO_MATCHING_ASSERTIONS", "No relevant graph assertions match the current discussion.", "[Graph Retrieval]"),
        ]

        for code, det, prefix in test_cases:
            res = _copilot_failure(
                error=code,
                details=det,
                machine=True,
                output_dir=out_dir,
                runtime_root=out_dir,
            )
            assert res["status"] == "FAIL"
            assert res["error"] == code
            assert res["user_message"].startswith(prefix)

        attempts_file = out_dir / "copilot_attempts.jsonl"
        assert attempts_file.is_file()
        lines = [json.loads(line) for line in attempts_file.read_text().strip().split("\n")]
        assert len(lines) == 4

        reasons = [item["terminal_reason"] for item in lines]
        assert reasons == ["SCOPE_FILTERED", "ENTITY_NOT_FOUND", "PARSER_NO_TOPIC", "ZERO_MATCHING_ASSERTIONS"]
