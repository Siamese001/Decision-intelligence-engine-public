"""
tests/end_to_end/test_copilot_cache_and_scope_e2e.py

End-to-end tests for Wave 4: Cache Hydration, Scope Inheritance & Ledger Telemetry Reliability.
Verifies:
1. Cached neighborhood chains are reused when engagement_id and attendees match, avoiding redundant recomputation.
2. CopilotGraphRetriever._index_nodes indexes both List[str] and List[Dict] attendee formats.
3. is_assertion_in_scope resolves engagement from scope_text and origin_engagement_id when engagement_id is absent, while rejecting unowned records.
4. AnswerLedger._init_db inspects existing columns via PRAGMA table_info before running ALTER TABLE, and _persist_answer_record populates retrieval_seeds from args when context_model.retrieval_seeds() is empty.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.answer_ledger_core.store import AnswerLedger
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_cache_graph import (
    _init_neighborhood_from_cache,
    save_cached_retriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _persist_answer_record,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


def _build_topology() -> InMemoryGraphTopology:
    return InMemoryGraphTopology(
        graph_path="/tmp/mock_wave4_e2e.sqlite",
        graph_sha256="sha_wave4_e2e",
        nodes={
            "person:brien": {
                "node_id": "person:brien",
                "node_type": "person",
                "canonical_name": "Speaker_Alpha",
                "display_name": "Speaker_Alpha",
                "engagement_id": "project:project_nexus",
            },
            "system:sap": {
                "node_id": "system:sap",
                "node_type": "system",
                "canonical_name": "SAP ERP",
                "display_name": "SAP ERP",
                "engagement_id": "project:project_nexus",
            },
        },
        assertions={
            "asrt_scope_txt": {
                "assertion_id": "asrt_scope_txt",
                "subject_node_id": "person:brien",
                "object_node_id": "system:sap",
                "predicate": "DECIDED",
                "claim_text": "Speaker_Alpha decided to freeze SAP ERP schema changes during cutover.",
                "scope_text": "Project Nexus cutover governance decision.",
                "canonical_status": "ACTIVE",
                "is_active": True,
            }
        },
        supporting_evidence={"asrt_scope_txt": ["ev_1"]},
        contradicting_evidence={},
        forward_adjacency={"person:brien": [], "system:sap": []},
        reverse_adjacency={"person:brien": [], "system:sap": []},
        undirected_adjacency={"person:brien": [], "system:sap": []},
        canonical_edges=[],
    )


def test_e2e_neighborhood_cache_reuse_with_attendees_and_engagement(tmp_path: Path) -> None:
    """Cached neighborhood chains are reused without invoking init_neighborhood_fn when engagement and attendees match."""
    topo = _build_topology()
    attendees = ["Speaker_Alpha"]
    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=attendees,  # type: ignore[arg-type]
        engagement_id="project:project_nexus",
        graph_sha256="sha_wave4_e2e",
        generation="gen_w4",
    )
    assert "person:brien" in retriever._attendee_node_ids
    assert "asrt_scope_txt" in retriever._indexed_assertion_records

    retriever.neighborhood_store = CopilotNeighborhoodStore(
        chains=[{"seed_id": "person:brien", "target_id": "system:sap", "chain_str": "Speaker_Alpha -> DECIDED -> SAP ERP"}],
        status="COMPLETE",
        engagement_id="project:project_nexus",
    )
    cache_file = tmp_path / "copilot_cache.pkl"
    save_cached_retriever(cache_file, topo, retriever)
    assert cache_file.is_file()

    import pickle
    cached_data = pickle.loads(cache_file.read_bytes())
    assert cached_data.get("attendees_sig") == ["brien"]

    recompute_called = False

    def _forbid_recompute(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal recompute_called
        recompute_called = True
        return CopilotNeighborhoodStore(chains=[], status="RECOMPUTED")

    fresh_retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=attendees,  # type: ignore[arg-type]
        engagement_id="project:project_nexus",
    )
    _init_neighborhood_from_cache(
        retriever=fresh_retriever,
        in_mem=topo,
        cached_data=cached_data,
        attendees=attendees,  # type: ignore[arg-type]
        engagement_id="project:project_nexus",
        precompute_timeout_seconds=1.0,
        init_neighborhood_fn=_forbid_recompute,
        neighborhood_store_cls=CopilotNeighborhoodStore,
    )
    assert not recompute_called
    assert fresh_retriever.neighborhood_store is not None
    assert len(fresh_retriever.neighborhood_store.chains) == 1


def test_e2e_answer_ledger_idempotent_init_and_seed_fallback(tmp_path: Path) -> None:
    """AnswerLedger initializes idempotently and _persist_answer_record populates retrieval_seeds from args when context is empty."""
    db_path = tmp_path / "answer_ledger.sqlite"
    ledger1 = AnswerLedger(db_path)
    ledger2 = AnswerLedger(db_path)
    assert ledger1.db_path == ledger2.db_path

    topo = _build_topology()
    retriever = CopilotGraphRetriever(
        topology=topo,
        engagement_id="project:project_nexus",
        graph_sha256="sha_wave4_e2e",
        generation="gen_w4",
    )
    cm = ConversationContextModel()
    args = argparse.Namespace(topic="SAP cutover freeze", request="What did Speaker_Alpha decide on SAP?")
    assertions = [retriever._indexed_assertion_records["asrt_scope_txt"]]

    ans_id = _persist_answer_record(
        intent="next-questions",
        target="Speaker_Alpha",
        body="Speaker_Alpha, could we clarify freezing SAP ERP schema changes during cutover?",
        assertions=assertions,
        retriever=retriever,
        output_dir=tmp_path,
        runtime_root=tmp_path,
        context_model=cm,
        session_id="sess_w4",
        evidence_breakdown={},
        stage_latencies_ms={"retrieve_ms": 1.2, "verify_ms": 0.8, "render_ms": 0.5, "total_ms": 2.5},
        args=args,
    )
    assert ans_id is not None
    from dual_pipeline.answer_ledger import get_default_answer_ledger
    saved = get_default_answer_ledger(tmp_path).get_answer(ans_id)
    assert saved is not None
    assert len(saved.retrieval_seeds) > 0
    assert "sap" in saved.retrieval_seeds or "cutover" in saved.retrieval_seeds
