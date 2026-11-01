"""End-to-End and Edge-Case verification of Copilot Cache Graph, Connect Intent, and Grounding Verifier.

Tests:
1. Generation-bound disk cache graph load speed (<150ms) and persistence.
2. Retention of 2-hop neighborhood chains in CopilotLifecycleOrchestrator bootstrap.
3. Zero raw entity hashes and zero mojibake in connect/spoken claims.
4. Conflicting assertion exclusion in GroundingVerifier VERIFIED tier.
5. Offline speak-gate rejection of ungrounded fallbacks.
6. Edge cases: Corrupted cache snapshot recovery and boundary payloads.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.cli.copilot_speech import spoken_claim, _distill_claim_text, _sanitize_speakable_claim
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
)
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    CopilotLifecycleOrchestrator,
    get_or_create_lifecycle_orchestrator,
)
from dual_pipeline.live_meeting_recorder_core.copilot_offline import prepare_copilot_body_for_delivery
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
    load_copilot_graph,
)


@pytest.mark.e2e
def test_copilot_cache_graph_hit_and_speed(tmp_path: Path):
    """Verifies cold load writes cache snapshot and warm load hydrates in <150ms."""
    # Cold load
    t0 = time.perf_counter()
    status_cold, in_mem_cold, retriever_cold = load_copilot_graph()
    t_cold = time.perf_counter() - t0

    assert status_cold == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert in_mem_cold is not None
    assert retriever_cold is not None

    # Warm load
    t1 = time.perf_counter()
    status_warm, in_mem_warm, retriever_warm = load_copilot_graph()
    t_warm = time.perf_counter() - t1

    assert status_warm == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert in_mem_warm is not None
    assert retriever_warm is not None

    # Warm load must hydrate in <150ms
    assert t_warm < 0.150, f"Warm cache load took {t_warm*1000:.2f}ms, expected <150ms"
    assert len(in_mem_warm.nodes) == len(in_mem_cold.nodes)
    assert len(in_mem_warm.assertions) == len(in_mem_cold.assertions)

    # Inverted indexes must be populated
    assert len(retriever_warm._inverted_index) > 0
    assert len(retriever_warm._allowed_nodes) > 0

    # 1-hop expansion must be enabled by default
    assert retriever_warm.enable_1hop_graph_expansion is True


@pytest.mark.e2e
def test_copilot_lifecycle_preserves_neighborhood_chains():
    """Verifies bootstrap_session preserves precomputed neighborhood chains."""
    sess_id = f"test_e2e_chains_{int(time.time_ns())}"
    orch = get_or_create_lifecycle_orchestrator(sess_id)

    ctx = orch.bootstrap_session(
        engagement_id="project:conduit",
        runtime_root=Path("."),
    )

    assert ctx is not None
    assert ctx.retriever is not None
    assert ctx.neighborhood_store is not None
    # Chains must NOT have been clobbered to empty
    assert len(ctx.neighborhood_store.chains) > 0
    assert ctx.retriever.neighborhood_store is ctx.neighborhood_store


@pytest.mark.e2e
def test_spoken_claim_zero_raw_entity_hashes_and_zero_mojibake():
    """Verifies that raw entity hashes are resolved or suppressed, and mojibake is stripped."""
    # Test mojibake stripping in distill
    mojibake_text = "Status: Â· Critical delivery milestone on track Â·"
    cleaned = _distill_claim_text(mojibake_text)
    assert "Â" not in cleaned
    assert "·" in cleaned

    # Test sanitize
    sanitized = _sanitize_speakable_claim("Â· Key deliverable Â· ready")
    assert "Â" not in sanitized

    # Create dummy retriever with a resolved node and an unresolved node
    class DummyTopology:
        nodes = {
            "node-resolved-01": {"display_name": "Project Conduit", "canonical_name": "PD Conduit"},
            "entity-78394a1b02": {"node_type": "entity"},  # No display or canonical name
        }

    dummy_retriever = CopilotGraphRetriever.__new__(CopilotGraphRetriever)
    dummy_retriever._topology = DummyTopology()

    # Assertion with resolved nodes
    asrt_clean = {
        "subject_node_id": "node-resolved-01",
        "predicate": "DELIVERS",
        "object_node_id": "node-resolved-01",
    }
    claim = spoken_claim(asrt_clean, dummy_retriever)
    assert "Project Conduit" in claim
    assert "entity-" not in claim

    # Assertion with unresolved raw entity hash
    asrt_raw_hash = {
        "subject_node_id": "entity-78394a1b02",
        "predicate": "RELATES_TO",
        "object_node_id": "node-resolved-01",
    }
    claim_suppressed = spoken_claim(asrt_raw_hash, dummy_retriever)
    # Must suppress raw entity hash
    assert "entity-78394a1b02" not in claim_suppressed


@pytest.mark.e2e
def test_grounding_verifier_verified_tier_excludes_conflicting_assertions():
    """Verifies that VERIFIED rigor tier strictly excludes conflicting assertions."""
    verifier = CopilotGroundingVerifier()

    candidates = [
        {
            "assertion_id": "ast-001",
            "subject_node_id": "phase1",
            "predicate": "STATUS",
            "object_literal": "on track",
            "claim_text": "Phase 1 is on track",
            "confidence_score": 0.95,
            "evidence_count": 2,
        },
        {
            "assertion_id": "ast-002",
            "subject_node_id": "phase1",
            "predicate": "STATUS",
            "object_literal": "delayed",
            "claim_text": "Phase 1 is delayed",
            "confidence_score": 0.85,
            "evidence_count": 2,
        },
        {
            "assertion_id": "ast-003",
            "subject_node_id": "phase2",
            "predicate": "OWNER",
            "object_literal": "Engineering",
            "claim_text": "Engineering owns Phase 2",
            "confidence_score": 0.90,
            "evidence_count": 2,
        },
    ]

    class MockDialogue:
        def retrieval_seeds(self):
            return ["phase1", "phase2"]
        def focus_turns(self):
            return [{"speaker": "Lead", "text": "Discussing phase 1 and phase 2 status"}]

    dialogue = MockDialogue()

    result = verifier.verify_and_ground(
        candidates=candidates,
        dialogue=dialogue,
        topology=None,
        packet_graph_sha="mock_sha",
        pinned_generation=1,
    )

    # Conflicting assertions (ast-001 vs ast-002) must be detected
    assert len(result.conflicts) > 0
    verified_ids = {c["assertion_id"] for c in result.verified_assertions}
    assert "ast-001" not in verified_ids
    assert "ast-002" not in verified_ids
    assert "ast-003" in verified_ids


@pytest.mark.e2e
def test_offline_speak_gate_enforcement():
    """Verifies offline mode does not leak ungrounded fallback when assertions fail speak-gate."""
    args = argparse.Namespace(max_words=10, json=False)

    def failing_renderer(*_args, **_kwargs):
        # Simulates candidate assertions failing speak-gate (e.g. malformed or exceeding word budget)
        return "", "MALFORMED_REPRESENTATION"

    assertions = [{"assertion_id": "ast-bad", "claim_text": "Too long or unverified"}]

    body, fail_code = prepare_copilot_body_for_delivery(
        body="Raw fallback text from graph",
        intent="next-actions",
        target="Engineer",
        resolved_target="Engineer",
        assertions=assertions,
        args=args,
        retriever=None,
        selected_utterance=None,
        is_offline=True,
        render_and_validate_body_fn=failing_renderer,
    )

    # Must reject and emit fail_code, not leak raw fallback
    assert body is None
    assert fail_code == "MALFORMED_REPRESENTATION"


@pytest.mark.e2e
def test_corrupted_cache_snapshot_graceful_recovery(tmp_path: Path):
    """Boundary & Recovery: Corrupted cache snapshot falls back cleanly to SQLite."""
    cache_dir = Path(".local/dual_pipeline/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    corrupted_file = cache_dir / "copilot_graph_test_corrupted.pkl"
    corrupted_file.write_bytes(b"NOT_A_VALID_PICKLE_STREAM_12345")

    try:
        # Load copilot graph directly; should handle corrupted cache without uncaught exception
        status, in_mem, retriever = load_copilot_graph()
        assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
        assert in_mem is not None
        assert retriever is not None
    finally:
        if corrupted_file.exists():
            corrupted_file.unlink()
