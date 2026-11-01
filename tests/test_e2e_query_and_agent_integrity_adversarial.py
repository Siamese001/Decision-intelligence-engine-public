"""Adversarial and property-based tests for Wave 2 Query Router, Pre-L2 Block & Agent Fallbacks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.query_router import QueryDestination, route_query
from dual_pipeline.retrieval.agents.multi_perspective_dispatcher import MultiPerspectiveDispatcher


def test_adversarial_pre_l2_blocked_sets_judge_release_passed_false() -> None:
    """Category: Invariant bypass / Exact production regression — _finish_pre_l2_blocked never reports judge_release_passed=True."""
    engine_src = Path("dual_pipeline/query_core/engine.py").read_text(encoding="utf-8")
    # Extract the _finish_pre_l2_blocked method block up to the next method def
    start_idx = engine_src.index("def _finish_pre_l2_blocked(")
    next_def_idx = engine_src.index("\n    def ", start_idx + 1)
    fn_block = engine_src[start_idx:next_def_idx]

    assert '"judge_release_passed": True' not in fn_block
    assert fn_block.count('"judge_release_passed": False') == 2


def test_adversarial_negated_graph_handoff_still_routes_to_general_assistant() -> None:
    """Category: Boundary payload & Invariant bypass — negated graph clause with general verb stays GENERAL_ASSISTANT_HANDOFF."""
    d1 = route_query("Without consulting governed graph relationships, draft a recommendation across options.")
    assert d1.destination == QueryDestination.GENERAL_ASSISTANT_HANDOFF

    d2 = route_query("Condense the meeting discussion into action items.")
    assert d2.destination == QueryDestination.GENERAL_ASSISTANT_HANDOFF

    d3 = route_query("Publish the current branch to the remote.")
    assert d3.destination == QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF


def test_adversarial_multi_perspective_dispatcher_cloud_exception_records_fallback() -> None:
    """Category: Malformed input / Provider crash recovery — cloud path records fallback_used and degraded_reason."""
    with patch("storage.credentials.CredentialManager.is_local_execution_mode", return_value=False):
        dispatcher = MultiPerspectiveDispatcher(api_key="sk-test-dummy", mock_mode=False, use_local_model=False)
    task = A2ATask(
        task_id="task-cloud-err",
        parent_task_id=None,
        sender_agent_id="router",
        recipient_agent_id="dispatcher",
        objective="Deconstruct decision query",
        status=A2ATaskStatus.WORKING,
        input_payload={"query": "Evaluate platform architecture risks across workstreams"},
    )
    with patch(
        "config.credentials.get_async_openai_client",
        side_effect=ConnectionError("simulated upstream socket reset"),
    ):
        res = asyncio.run(dispatcher.handle_task(task))

    assert res["task_id"] == "task-cloud-err"
    assert res["fallback_used"] is True
    assert "ConnectionError: simulated upstream socket reset" in res["degraded_reason"]
    assert len(res["sub_queries"]) == 1


@given(
    target_topic=st.sampled_from(
        [
            "platform architecture decision",
            "cloud migration cutover",
            "grid modernization investment",
            "cross-workstream governance model",
        ]
    )
)
@settings(max_examples=10, deadline=None)
def test_property_strategic_multi_record_queries_route_deterministically(target_topic: str) -> None:
    """Category: Property-based routing stability across strategic multi-record queries."""
    q = f"Evaluate the strategic trade-offs and material risks across multiple records for the {target_topic}."
    res = route_query(q)
    assert res.destination == QueryDestination.NEE_DECISION_INTELLIGENCE
    assert res.enters_query_runtime is True


@given(
    stage=st.sampled_from(["X_EXIT_GATES", "EVALS_RELEASE"]),
    had_validated=st.booleans(),
)
@settings(max_examples=10, deadline=None)
def test_property_document_checkpoint_failure_handling_preserves_or_invalidates_by_stage(
    stage: str, had_validated: bool
) -> None:
    """F-E2E-02 property check: X_EXIT_GATES failure invalidates document synthesis while downstream EVALS failure preserves validated synthesis."""
    import tempfile
    from dual_pipeline.canonical_pipeline.document_resume import (
        _ACTIVE_DOCUMENT_CHECKPOINT,
        record_document_failure,
    )
    from dual_pipeline.checkpoint import PipelineCheckpointManager

    with tempfile.TemporaryDirectory() as td:
        ckpt_path = Path(td) / "doc_ckpt.json"
        ckpt = PipelineCheckpointManager(ckpt_path)
        if had_validated:
            ckpt.save_stage("VALIDATED_SYNTHESIS", {"attempt": 1, "run_id": "r1"})
        _ACTIVE_DOCUMENT_CHECKPOINT.set(ckpt)

        record_document_failure({"run_id": "r1"}, stage, RuntimeError("simulated stage failure"))
        reloaded = PipelineCheckpointManager(ckpt_path)
        last_fail = reloaded.get_stage("LAST_FAILURE")
        assert last_fail is not None
        if stage == "X_EXIT_GATES":
            assert not reloaded.has_stage("VALIDATED_SYNTHESIS")
            assert last_fail["reason"] == "X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED"
        elif had_validated:
            assert reloaded.has_stage("VALIDATED_SYNTHESIS")
            assert last_fail["reason"] == "DOWNSTREAM_FAILURE_REUSE_VALIDATED_SYNTHESIS"
        else:
            assert last_fail["reason"] == "NO_VALIDATED_SYNTHESIS"


def test_adversarial_canonical_pipeline_role_library_auto_triggers_5_agent_once() -> None:
    """Category: Boundary payload & Invariant bypass — role-library title triggers 5-agent pre-pass even without explicit enable_5_agent_intelligence=True and never re-runs."""
    from types import SimpleNamespace
    from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
        inject_5_agent_intelligence_if_needed,
    )

    doc_text = "Detailed Agile Delivery Roles governance specification. " * 15
    meta = {
        "title": "Enterprise Agile Delivery Roles Catalog",
        "provider_type": "offline",
    }
    invocations: list[dict] = []

    def _fake_run(document_text: str, document_title: str = "Document", **kwargs):
        invocations.append({"title": document_title, **kwargs})
        return SimpleNamespace(
            coverage_score=0.91,
            primacy_bias_detected=False,
            total_entities_detected=1,
            entities=[{"id": "role:product_owner", "type": "Role", "name": "Product Owner", "description": "Owns backlog"}],
            relationships=[],
            arbiter_verdict="PASS",
        )

    with patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.run_5_agent_document_intelligence",
        side_effect=_fake_run,
    ):
        inject_5_agent_intelligence_if_needed(doc_text, meta)
        inject_5_agent_intelligence_if_needed(doc_text, meta)

    assert len(invocations) == 1
    assert invocations[0].get("provider_type") == "offline"
    assert "5-AGENT MULTI-ENTITY DECOMPOSITION MANIFEST" in meta.get("prior_context", "")


def test_adversarial_decision_http_server_with_lazy_decision_service_checks_active_graph(tmp_path: Path) -> None:
    """E2E-CRIT-03: Verifies DecisionHTTPServer.deep_health_check resolves active_graph when backed by LazyDecisionService."""
    import json
    import sqlite3
    from dual_pipeline.decision_api import DecisionHTTPServer
    from dual_pipeline.decision_service import DecisionService, LazyDecisionService

    gen_dir = tmp_path / "generations" / "gen-lazy"
    gen_dir.mkdir(parents=True)
    graph_db = gen_dir / "graph.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY)")

    (tmp_path / "active_pair.json").write_text(
        json.dumps({"generation_id": "gen-lazy", "graph_path": "generations/gen-lazy/graph.sqlite"}),
        encoding="utf-8",
    )

    lazy_svc = LazyDecisionService(
        runtime_root=tmp_path,
        runtime_service_factory=lambda: DecisionService(query_engine=None, runtime_root=tmp_path),
    )
    server = DecisionHTTPServer(("127.0.0.1", 0), lazy_svc)
    try:
        healthy, payload = server.deep_health_check()
        assert healthy is True
        assert payload["status"] == "HEALTHY"
        assert payload["checks"]["sqlite"] == "OK"
        assert payload["checks"]["sqlite_target"] == "active_graph"
    finally:
        server.server_close()


def test_adversarial_hash_cache_fallback_signing_key_uses_ephemeral_random_bytes(tmp_path: Path) -> None:
    """E2E-CRIT-03: Verifies GenerationalHashCache generates unique ephemeral 32-byte random keys instead of hardcoded static bytes on in-memory or key-file failure."""
    from dual_pipeline.hash_cache import GenerationalHashCache

    c1 = GenerationalHashCache(cache_file=None, key_file=None)
    c2 = GenerationalHashCache(cache_file=None, key_file=None)
    assert len(c1._hmac_key) == 32
    assert len(c2._hmac_key) == 32
    assert c1._hmac_key != c2._hmac_key
    assert b"in_memory" not in c1._hmac_key

    unwritable_key = tmp_path / "nonexistent_dir" / "key"
    with patch("pathlib.Path.write_bytes", side_effect=OSError("read-only filesystem")):
        c3 = GenerationalHashCache(cache_file=tmp_path / "verdicts.json", key_file=unwritable_key)
        c4 = GenerationalHashCache(cache_file=tmp_path / "verdicts.json", key_file=unwritable_key)
    assert len(c3._hmac_key) == 32
    assert c3._hmac_key != c4._hmac_key
    assert b"in_memory" not in c3._hmac_key


