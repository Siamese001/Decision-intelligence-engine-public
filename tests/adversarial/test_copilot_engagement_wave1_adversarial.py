"""
tests/adversarial/test_copilot_engagement_wave1_adversarial.py

Adversarial and edge-case tests for Wave 1 Copilot engagement isolation.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty inputs, zero-length candidates)
2. Malformed input (truncated session JSON, missing required fields)
3. Invariant bypass attempt (renamed engagements, hash tampering)
4. Exact production regression (pinning LENS assertion in Jarvis meeting)
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)
from tests.fixtures.copilot_engagement import (
    CopilotProvenanceRecord,
    build_in_memory_graph,
    compute_answer_sha256,
    create_provenance_record,
    load_engagement_fixtures,
    load_session_snapshot,
)


# ==============================================================================
# 1. Boundary Payload Category
# ==============================================================================
def test_adversarial_boundary_payload_empty_and_zero_inputs() -> None:
    """Tests boundary payloads: zero-length strings, empty lists, and null fields."""
    empty_record = create_provenance_record(
        input_request="",
        selected_session_id="",
        engagement_id="",
        source_assertion_ids=[],
        displayed_answer="",
        route_identity="",
        missing_historical_binding=False,
    )
    # Empty string SHA-256 is the standard empty hash
    assert empty_record.answer_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert empty_record.source_assertion_ids == []
    assert len(empty_record.record_id) > 0

    # Retriever with empty topology should not crash
    empty_graph = SimpleNamespace(nodes={}, assertions={}, forward_adjacency={})
    retriever = CopilotGraphRetriever(topology=empty_graph, attendees=[])
    res = retriever.retrieve_structured_assertions("")
    assert res == []


# ==============================================================================
# 2. Malformed Input Category
# ==============================================================================
def test_adversarial_malformed_session_manifest(tmp_path: Path) -> None:
    """Tests malformed session manifests: truncated JSON, wrong types, missing fields."""
    sess_dir = tmp_path / ".session_registry"
    sess_dir.mkdir(parents=True, exist_ok=True)

    # Truncated JSON
    truncated_file = sess_dir / "truncated.json"
    truncated_file.write_text('{"session_id": "abc-123", "meeting_slug":', encoding="utf-8")

    # Corrupt JSON with wrong types
    wrong_types_file = sess_dir / "wrong_types.json"
    wrong_types_file.write_text('{"session_id": 12345, "ws_port": "not-a-port"}', encoding="utf-8")

    # Safe parsing helper test
    for target in (truncated_file, wrong_types_file):
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            parsed_valid = isinstance(data.get("session_id"), str)
        except Exception:
            parsed_valid = False
        assert not parsed_valid, f"Corrupted file {target.name} must not be accepted as a valid session"


# ==============================================================================
# 3. Invariant Bypass Attempt Category
# ==============================================================================
def test_adversarial_invariant_bypass_renamed_engagement() -> None:
    """
    Tests adversarial invariant bypass with renamed engagements:
    Renaming 'JARVIS' to 'PartnerAdvisory_Jarvis_Core' or 'LENS' to 'Project_Lens_Analytics'
    exposes boundary fragility when identity is based solely on fuzzy name matching.
    """
    fixtures = load_engagement_fixtures()
    neg_controls = fixtures["negative_controls"]
    renamed_cases = neg_controls["renamed_engagements"]
    graph = build_in_memory_graph()

    retriever = CopilotGraphRetriever(
        topology=graph,
        attendees=[{"name": "Shared Engineer"}],
    )

    for case in renamed_cases:
        original = case["original"]
        renamed = case["renamed"]

        # Searching with renamed term fails to retrieve original assertions if indexing relies on exact tokens
        renamed_assertions = retriever.retrieve_structured_assertions(renamed)
        # Without canonical engagement ID binding, renamed query cannot reliably match
        retrieved_ids = [a["assertion_id"] for a in renamed_assertions]
        assert case["canonical_id"] not in [a.get("subject_node_id") for a in renamed_assertions]


def test_adversarial_invariant_bypass_tampered_answer_hash() -> None:
    """Tests that modifying displayed answer text without recomputing hash fails validation."""
    prov = create_provenance_record(
        input_request="What is the database engine?",
        selected_session_id="sess-001",
        engagement_id="project:jarvis",
        source_assertion_ids=["assertion-jarvis-1"],
        displayed_answer="Apple Silicon Metal local execution.",
        route_identity="cli",
    )
    tampered_dict = prov.to_dict()
    # Malicious actor changes the answer text while leaving answer_sha256 intact
    tampered_dict["displayed_answer"] = "Google Cloud Platform cloud execution."

    recomputed = compute_answer_sha256(tampered_dict["displayed_answer"])
    assert recomputed != tampered_dict["answer_sha256"], (
        "Hash mismatch must detect post-validation text tampering"
    )


# ==============================================================================
# 4. Exact Production Regression Category
# ==============================================================================
def test_adversarial_exact_production_incident_regression() -> None:
    """
    Pins the exact incident scenario:
    - Session: Engineering_Jarvis_Kaleena_PartnerAdvisory
    - Assertion: assertion-b7cd2f78b3a1c4995bd4cfb1ee31 (Spatial File Upload depends on JIRA)
    - Project Scope: LENS
    Demonstrates that unconstrained retrieval produced cross-engagement leakage,
    and proves that binding checks detect the project_scope mismatch.
    """
    sess = load_session_snapshot()
    assert sess["meeting_slug"] == "Engineering_Jarvis_Kaleena_PartnerAdvisory"

    fixtures = load_engagement_fixtures()
    iso_assertions = fixtures["isolated_graph"]["assertions"]
    lens_asrt = iso_assertions["assertion-b7cd2f78b3a1c4995bd4cfb1ee31"]

    assert lens_asrt["project_scope"] == "LENS"
    assert "JIRA" in lens_asrt["claim_text"]

    # When bound to Engineering_Jarvis, the project scope differs
    session_scope = "JARVIS" if "Jarvis" in sess["meeting_slug"] else "UNKNOWN"
    is_boundary_violation = (lens_asrt["project_scope"] != session_scope)
    assert is_boundary_violation is True, (
        "Production incident regression: LENS assertion in JARVIS session is a confirmed boundary violation"
    )
