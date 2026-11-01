"""
tests/test_copilot_engagement_wave1.py

Wave 1 Acceptance Tests: Preserve provenance, capture final answer hashes and route
identities, reproduce boundary leakage in isolated graph, and distinguish helper
reproduction from incident replay with explicit missing historical bindings.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    compute_neighborhood_chains,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
)
from tests.fixtures.copilot_engagement import (
    CopilotProvenanceRecord,
    build_in_memory_graph,
    compute_answer_sha256,
    create_provenance_record,
    load_evidence_manifest,
    load_incident_answers,
    load_session_snapshot,
    verify_fixture_digests,
)


def test_frozen_fixtures_digest_integrity() -> None:
    """Verifies that all frozen incident fixtures match cryptographic hashes."""
    results = verify_fixture_digests()
    for filename, check in results.items():
        assert check.get("status") == "MATCH", f"Fixture {filename} failed: {check}"
    manifest = load_evidence_manifest()
    assert manifest.get("status") == "INVESTIGATION_ONLY"
    assert "sources" in manifest
    assert len(manifest["sources"]) == 2


def test_incident_answers_attribution_and_hashes() -> None:
    """Validates answer hashes, cited assertions, and contaminated answers in incident history."""
    answers = load_incident_answers()
    assert len(answers) == 8, f"Expected 8 incident answers, got {len(answers)}"

    lens_contaminated_count = 0
    answer_hashes: List[str] = []

    for item in answers:
        resp = item.get("response", "")
        assert resp, "Response text must not be empty"
        h = compute_answer_sha256(resp)
        answer_hashes.append(h)
        assert len(h) == 64

        cited = item.get("cited_assertion_ids", [])
        assert isinstance(cited, list)

        # Check for observed LENS assertions in the Engineering Jarvis answer history
        if "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" in cited or "Spatial File Upload" in resp:
            lens_contaminated_count += 1
        if "assertion-6edba0488c78801f0085e8dfb268" in cited or "Google PartnerAdvisory apex_global Lens" in resp:
            lens_contaminated_count += 1

    # Answers 1 and 7 in the incident have the exact same response text
    assert len(set(answer_hashes)) == 7, "Incident contains 7 unique hashes across 8 records (1 repeated response)"


def test_explicit_unresolved_historical_binding() -> None:
    """Verifies that the three-issue narrative is explicitly unbound rather than fabricated."""
    answers = load_incident_answers()
    three_issue_needle = "At Apex Global, SecOps review and enterprise identity provisioning"

    # Verify that the pasted prose is absent from the 8 saved JSONL records
    found_in_snapshots = any(three_issue_needle in str(a.get("response", "")) for a in answers)
    assert not found_in_snapshots, "Pasted prose should not match any of the 8 saved JSONL answers"

    # Construct explicit unbound provenance record
    sess = load_session_snapshot()
    record = create_provenance_record(
        input_request="Review of SecOps timing and JIRA dependencies",
        selected_session_id=sess["session_id"],
        engagement_id="project:jarvis",
        source_assertion_ids=[],
        displayed_answer=three_issue_needle,
        route_identity="unbound_cli_or_host",
        missing_historical_binding=True,
        unresolved_attribution_note=(
            "Exact pasted narrative absent from the eight snapshotted records; "
            "original invocation and rendered paragraph remain unbound."
        ),
    )
    assert record.missing_historical_binding is True
    assert record.unresolved_attribution_note is not None
    assert record.answer_sha256 == compute_answer_sha256(three_issue_needle)


def test_reproduce_boundary_leakage_helper_in_memory() -> None:
    """
    Demonstrates in-memory helper reproduction: shared terminology and shared attendees
    pull LENS assertions into JARVIS context under unconstrained retrieval.
    """
    graph = build_in_memory_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        attendees=[{"name": "Shared Engineer"}],
    )

    # 1. Terminology-driven boundary leakage: searching for JIRA in JARVIS
    assertions = retriever.retrieve_structured_assertions("JIRA dependencies")
    retrieved_ids = [a["assertion_id"] for a in assertions]
    assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" in retrieved_ids, (
        "Expected LENS assertion 'Spatial File Upload depends on JIRA' to leak into results"
    )

    # 2. Shared-attendee neighborhood traversal: prefetch includes LENS chains
    bridge = CopilotPrefetchBridge(retriever=retriever)
    packet = bridge.prefetch(
        project_name="JARVIS",
        attendees=[{"name": "Shared Engineer"}],
    )
    assert packet.project_scope == "JARVIS"

    chains, status = compute_neighborhood_chains(
        topology=graph,
        seed_node_ids=["person:shared"],
    )
    target_ids = [c.get("target_id") for c in chains]
    assert "project:lens" in target_ids, "Neighborhood traversal from shared person reached project:lens"

    # Formatted prompt contains LENS content under JARVIS header
    prompt_str = packet.format_for_copilot_prompt()
    assert "JARVIS" in prompt_str
    assert "LENS" in prompt_str, "Unfiltered prefetch packet leaked LENS into prompt"


def test_negative_control_unrelated_engagement() -> None:
    """Verifies that completely unrelated engagement (Solar) does not leak into JARVIS queries."""
    graph = build_in_memory_graph()
    retriever = CopilotGraphRetriever(
        topology=graph,
        attendees=[{"name": "Alex Rivera"}],
    )

    # Query for JARVIS topic
    jarvis_assertions = retriever.retrieve_structured_assertions("Apple Silicon transcription")
    asrt_ids = [a["assertion_id"] for a in jarvis_assertions]
    assert "assertion-jarvis-1" in asrt_ids
    assert "assertion-solar-1" not in asrt_ids, "Solar assertion must not leak into JARVIS query"

    # Query for Solar topic
    solar_assertions = retriever.retrieve_structured_assertions("commercial inverter voltage")
    solar_ids = [a["assertion_id"] for a in solar_assertions]
    assert "assertion-solar-1" in solar_ids
    assert "assertion-jarvis-1" not in solar_ids


def test_provenance_record_tamper_detection() -> None:
    """Verifies that tampering with displayed answer bytes invalidates hash integrity."""
    original_answer = "JARVIS executes local meeting transcription on Apple Silicon Metal."
    record = create_provenance_record(
        input_request="Where does transcription execute?",
        selected_session_id="test-session-123",
        engagement_id="project:jarvis",
        source_assertion_ids=["assertion-jarvis-1"],
        displayed_answer=original_answer,
        route_identity="cli",
    )
    assert record.answer_sha256 == compute_answer_sha256(original_answer)

    # Tampered answer must produce mismatched digest
    tampered_answer = original_answer + " (unverified addition)"
    tampered_hash = compute_answer_sha256(tampered_answer)
    assert record.answer_sha256 != tampered_hash
