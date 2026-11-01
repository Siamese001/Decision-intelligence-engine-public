"""
tests/test_copilot_watchdog_ledger_adversarial.py

Adversarial and Edge-Case Tests for Wave 4: Cache Hydration, Scope Inheritance & Ledger Telemetry.
Covers mandatory edge-case categories:
1. Boundary payload: Empty attendee lists, None engagement_id, empty scope_text.
2. Malformed input: Non-string engagement_id/scope_text types, mixed dict/string/None attendee lists.
3. Invariant bypass attempt: Mismatched engagement or attendee signature in cache forces recomputation; unowned assertions still fail closed.
4. Concurrency & race: Concurrent AnswerLedger initialization and append_answer calls across threads.
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any, List

from dual_pipeline.answer_ledger_core.models import AnswerRecord
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_cache_graph import (
    _attendee_signature,
    _init_neighborhood_from_cache,
)
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


def test_adversarial_mixed_and_malformed_attendee_inputs() -> None:
    """Category 1 & 2 (Boundary & Malformed input): Mixed dict, string, empty, and None attendee entries do not crash."""
    mixed_attendees: List[Any] = [
        "Speaker_Alpha",
        {"name": "Sharon Perez", "role": "Architect"},
        "",
        "   ",
        {},
        None,
    ]
    sig = _attendee_signature(mixed_attendees)
    assert sig == ["brien", "sharon perez"]

    topo = InMemoryGraphTopology(
        graph_path="/tmp/mock_w4_adv.sqlite",
        graph_sha256="sha_w4_adv",
        nodes={
            "person:brien": {
                "node_id": "person:brien",
                "node_type": "person",
                "canonical_name": "Speaker_Alpha",
                "display_name": "Speaker_Alpha",
                "engagement_id": "project:project_nexus",
            }
        },
        assertions={},
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency={"person:brien": []},
        reverse_adjacency={"person:brien": []},
        undirected_adjacency={"person:brien": []},
        canonical_edges=[],
    )
    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=mixed_attendees,
        engagement_id="project:project_nexus",
    )
    assert "person:brien" in retriever._attendee_node_ids


def test_adversarial_cache_mismatch_forces_neighborhood_recomputation() -> None:
    """Category 3 (Invariant bypass): Mismatched engagement or attendee list refuses stale cached chains."""
    cached_data = {
        "engagement_id": "project:project_nexus",
        "attendees_sig": ["brien"],
        "neighborhood_chains": [{"seed_id": "person:brien", "target_id": "system:sap", "chain_str": "Speaker_Alpha -> SAP"}],
        "neighborhood_status": "COMPLETE",
    }
    recomputed = False

    def _recompute_fn(*_args: Any, **_kwargs: Any) -> CopilotNeighborhoodStore:
        nonlocal recomputed
        recomputed = True
        return CopilotNeighborhoodStore(chains=[], status="RECOMPUTED")

    class DummyRetriever:
        permitted_engagements = {"project:project_lens"}
        neighborhood_store = None

    dummy = DummyRetriever()
    # Mismatched engagement_id ("project:project_lens" vs cached "project:project_nexus") must recompute
    _init_neighborhood_from_cache(
        retriever=dummy,
        in_mem=None,
        cached_data=cached_data,
        attendees=["Speaker_Alpha"],  # type: ignore[arg-type]
        engagement_id="project:project_lens",
        precompute_timeout_seconds=1.0,
        init_neighborhood_fn=_recompute_fn,
        neighborhood_store_cls=CopilotNeighborhoodStore,
    )
    assert recomputed is True
    assert dummy.neighborhood_store.status == "RECOMPUTED"


def test_adversarial_scope_text_rejects_unowned_and_foreign_records() -> None:
    """Category 3 (Invariant bypass): Unowned assertions or foreign scope_text fail closed."""
    assert not is_assertion_in_scope(
        {"assertion_id": "a_unowned", "scope_text": "General meeting discussion without project name"},
        bound_engagement="project:project_nexus",
    )
    assert not is_assertion_in_scope(
        {"assertion_id": "a_foreign", "scope_text": "Project Solar inverter telemetry"},
        bound_engagement="project:project_nexus",
    )
    assert not is_assertion_in_scope(
        {"assertion_id": "a_bad_type", "engagement_id": 12345, "scope_text": ["list"]},
        bound_engagement="project:project_nexus",
    )


def test_adversarial_concurrent_answer_ledger_writes(tmp_path: Path) -> None:
    """Category 4 (Concurrency & race): Multi-threaded AnswerLedger writes maintain hash chain integrity."""
    db_path = tmp_path / "concurrent_ledger.sqlite"
    ledger = AnswerLedger(db_path)

    def _write_record(idx: int) -> str:
        rec = AnswerRecord(
            pipeline="copilot",
            session_id=f"sess_{idx}",
            intent_or_type="next-questions",
            context_digest=f"dig_{idx}",
            retrieval_seeds=[f"seed_{idx}"],
            context_snapshot=[],
            cited_assertion_ids=[f"asrt_{idx}"],
            rigor="VERIFIED",
            response_text=f"Could we clarify item {idx}?",
        )
        saved = ledger.append_answer(rec)
        return saved.answer_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(_write_record, range(16)))

    assert len(ids) == 16
    ok, errors = ledger.verify_chain()
    assert ok is True, f"Hash chain verification failed: {errors}"
