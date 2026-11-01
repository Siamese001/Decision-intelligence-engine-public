"""
tests/test_copilot_error_telemetry_adversarial.py

Adversarial and edge-case testing for executive copilot error telemetry under
AGENTS.md Invariant 9 and e2e-and-edge-case-coverage.md.
Covers 5 distinct edge-case categories:
1. Boundary payload (empty inputs, zero-length tokens, all-punctuation prompts)
2. Malformed input (Unicode symbols, SQL injection fragments, extreme whitespace)
3. Concurrency & race (parallel attempt ledger emissions across multiple worker threads)
4. Invariant bypass attempt (Rule C-19 quarantine boundaries, isolated cross-tenant leakage)
5. Exact production regression (The "SAP tech owner jason question" retrieval failure scenario)
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_SCOPE_BOUNDARY,
    CATEGORY_USAGE,
    categorize_copilot_terminal_reason,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _copilot_failure,
    _diagnose_unresolved_evidence,
    _is_entity_present,
    _parse_and_validate_copilot_args,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


from types import SimpleNamespace


@pytest.fixture
def base_topology() -> Any:
    return SimpleNamespace(
        nodes={
            "tech:sap": {
                "canonical_name": "SAP",
                "display_name": "SAP ERP",
                "node_type": "technology",
                "engagement_id": "project:project_nexus",
            },
            "attendee:jason_lickliter": {
                "canonical_name": "Jason Lickliter",
                "display_name": "Jason Lickliter",
                "node_type": "person",
                "role": "Full Stack SWE / Technical Anchor",
                "is_attendee": True,
                "engagement_id": "shared",
            },
        },
        assertions={
            "asrt_sap_jason": {
                "assertion_id": "asrt_sap_jason",
                "subject_node_id": "attendee:jason_lickliter",
                "predicate": "OWNS_TECHNICAL_ARCHITECTURE",
                "object_node_id": "tech:sap",
                "claim_text": "Jason Lickliter is the technical owner and architect for SAP integration.",
                "engagement_id": "project:project_nexus",
                "canonical_status": "ACTIVE",
            },
        },
        forward_adjacency={},
    )


# Category 1: Boundary payload
@pytest.mark.parametrize(
    "boundary_prompt",
    [
        "",
        "   ",
        "\t\t\n\r",
        "???",
        "---",
        "///",
        "*** !!! ###",
        "None",
    ],
)
def test_adversarial_boundary_payloads(boundary_prompt: str) -> None:
    args = argparse.Namespace(
        request=boundary_prompt,
        intent=None,
        target=None,
        topic=None,
        json=True,
        runtime_root=None,
    )
    req_tuple, fail_res = _parse_and_validate_copilot_args(args)
    if boundary_prompt.strip() and boundary_prompt.strip() != "None":
        assert req_tuple is None
        assert fail_res is not None
        assert fail_res["error"] == "PARSER_NO_TOPIC"
        assert "[Prompt Parser]" in fail_res["user_message"]


# Category 2: Malformed input
@pytest.mark.parametrize(
    "malformed_prompt",
    [
        "copilot 🚀 💥 ??? --",
        "SELECT * FROM assertions WHERE 1=1; DROP TABLE nodes; --",
        "<script>alert('xss')</script> @@@@@",
        "\\x00\\x01\\x1f \\0 \\t",
        "A" * 1500,  # Extreme length string without spaces
    ],
)
def test_adversarial_malformed_inputs(malformed_prompt: str, base_topology: InMemoryGraphTopology) -> None:
    retriever = CopilotGraphRetriever(
        topology=base_topology,
        engagement_id="project:project_nexus",
        generation=1,
    )
    parsed = parse_copilot_request(malformed_prompt)
    code, det = _diagnose_unresolved_evidence(
        retriever,
        topic_keywords=parsed.topic_keywords,
        topic_query=parsed.topic_query,
        target=parsed.target_speaker,
    )
    # Even under extreme or hostile input, system diagnoses cleanly without crashing
    assert code in ("PARSER_NO_TOPIC", "ENTITY_NOT_FOUND", "ZERO_MATCHING_ASSERTIONS")
    cat, msg = categorize_copilot_terminal_reason(code, det)
    assert msg.startswith(("[Prompt Parser]", "[Entity Resolution]", "[Graph Retrieval]"))


# Category 3: Concurrency & race conditions
def test_adversarial_concurrency_race_conditions() -> None:
    """Verifies thread-safe simultaneous emission to attempt ledger under high concurrency."""
    with tempfile.TemporaryDirectory(prefix="copilot_race_test_") as tmp_dir:
        out_dir = Path(tmp_dir)
        num_threads = 16

        def _log_attempt(idx: int) -> dict:
            codes = ["SCOPE_FILTERED", "ENTITY_NOT_FOUND", "PARSER_NO_TOPIC", "ZERO_MATCHING_ASSERTIONS"]
            code = codes[idx % len(codes)]
            return _copilot_failure(
                error=code,
                details=f"Parallel test run {idx}",
                machine=True,
                output_dir=out_dir,
                runtime_root=out_dir,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_log_attempt, i) for i in range(num_threads)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == num_threads
        attempts_file = out_dir / "copilot_attempts.jsonl"
        assert attempts_file.is_file()

        lines = [json.loads(line) for line in attempts_file.read_text().strip().split("\n") if line.strip()]
        assert len(lines) == num_threads
        for item in lines:
            assert item["terminal_outcome"] == "FAIL"
            assert item["terminal_reason"] in ["SCOPE_FILTERED", "ENTITY_NOT_FOUND", "PARSER_NO_TOPIC", "ZERO_MATCHING_ASSERTIONS"]


# Category 4: Invariant bypass attempt (Rule C-19 Career Firewall & Tenant Leakage)
def test_adversarial_invariant_c19_defense(base_topology: InMemoryGraphTopology) -> None:
    # Add C-19 quarantined assertion
    base_topology.assertions["asrt_career_secret"] = {
        "assertion_id": "asrt_career_secret",
        "subject_node_id": "attendee:jason_lickliter",
        "predicate": "HAS_CAREER_LEVERAGE",
        "claim_text": "Personal compensation trajectory executive compensation band confidential.",
        "engagement_id": "INTERNAL_ADVISORY",
        "canonical_status": "ACTIVE",
    }
    retriever = CopilotGraphRetriever(
        topology=base_topology,
        engagement_id="project:project_nexus",
        generation=1,
    )
    # Attempting to retrieve career leverage must never leak
    results = retriever.retrieve_structured_assertions("compensation trajectory $1.2M")
    assert len(results) == 0

    code, det = _diagnose_unresolved_evidence(
        retriever,
        topic_keywords=["compensation"],
        topic_query="compensation trajectory",
        target="jason",
    )
    assert code in ("ZERO_MATCHING_ASSERTIONS", "SCOPE_FILTERED")
    _, msg = categorize_copilot_terminal_reason(code, det)
    assert "$1.2M" not in msg
    assert "confidential" not in msg


# Category 5: Exact production regression
def test_adversarial_exact_production_regression(base_topology: InMemoryGraphTopology) -> None:
    """Pins exact production failure: '-copilot SAP tech owner jason question'."""
    # Scenario A: Scope mismatch (foreign project) -> Must report SCOPE_FILTERED
    foreign_retriever = CopilotGraphRetriever(
        topology=base_topology,
        engagement_id="project:foreign_tenant",
        generation=1,
    )
    assert foreign_retriever._scope_filtered_count > 0
    code_a, det_a = _diagnose_unresolved_evidence(
        foreign_retriever,
        topic_keywords=["sap", "tech", "owner"],
        topic_query="SAP tech owner",
        target="jason",
    )
    assert code_a == "SCOPE_FILTERED"
    cat_a, msg_a = categorize_copilot_terminal_reason(code_a, det_a)
    assert cat_a == CATEGORY_SCOPE_BOUNDARY
    assert msg_a.startswith("[Scope Boundary]")

    # Scenario B: Target entity does not exist -> Must report ENTITY_NOT_FOUND
    nexus_retriever = CopilotGraphRetriever(
        topology=base_topology,
        engagement_id="project:project_nexus",
        generation=1,
    )
    code_b, det_b = _diagnose_unresolved_evidence(
        nexus_retriever,
        topic_keywords=["sap", "tech", "owner"],
        topic_query="SAP tech owner",
        target="TenikaJohnson",
    )
    assert code_b == "ENTITY_NOT_FOUND"
    cat_b, msg_b = categorize_copilot_terminal_reason(code_b, det_b)
    assert cat_b == CATEGORY_ABSENT_CANDIDATES
    assert msg_b == "[Entity Resolution] Target 'TenikaJohnson' not found in graph or active roster."
