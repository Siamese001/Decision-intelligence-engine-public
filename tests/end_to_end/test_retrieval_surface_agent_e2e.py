"""tests/end_to_end/test_retrieval_surface_agent_e2e.py

End-to-end verification for the Retrieval Agent (`retrieval_surface_assurance`)
and its two-phase remediation across the repository's 7 retrieval surfaces:
1. Canonical Retrieval Agent specification & SSOT pointer registration.
2. Surface 2 (`RetrievalCoordinator`): perspective-specific ranking, perspective
   starvation healing (`RetrievalPerspectiveHealerAgent`), and full 11-gate
   strict release qualification (`ReleaseGateAuditor`).
3. Surface 5 (`PTCHybridQueryExecutor`, `evaluate_candidate_retrieval`,
   `compute_hybrid_scoring`): `RetrievedFact` dataclass normalization, role
   boundary enforcement, subgraph depth traversal, 3-modality BGE-M3 scoring,
   and empirical 11-gate candidate evaluation.
4. Surface 1 (`BgeRerankerService`): bounded LRU oldest-entry eviction with
   touch-on-hit promotion instead of full cache flush at capacity.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List

from dual_pipeline.agent_sdk.tools.retrieval_tools import (
    compute_hybrid_scoring,
    evaluate_candidate_retrieval,
)
from dual_pipeline.ptc.hybrid_query import PTCHybridQueryExecutor, PTCHybridQueryPlan
from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor
from dual_pipeline.retrieval.agents.retrieval_coordinator import RetrievalCoordinator
from dual_pipeline.retrieval.bge_reranker_service import (
    _MAX_PAIR_CACHE_ENTRIES,
    _PAIR_SCORE_CACHE,
    _RERANKER_MODEL_CACHE,
    BgeRerankerService,
)
from pipeline_c_retrieval.contracts import RetrievedFact
from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, run_prompt_ssot_lint
from tools.scheduled_task_core.contracts import TWELVE_GOVERNED_TASK_IDS, get_task_definition
from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter
from tools.task_restoration_core.ssot_pointers import (
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _E2EHybridSearchEngine:
    def __init__(self, items: List[Any]) -> None:
        self._items = items

    def search(self, **kwargs: Any) -> List[Any]:
        return list(self._items)


class _E2ERerankerBackend:
    def compute_score(self, pairs: List[tuple[str, str]], normalize: bool = True) -> List[float]:
        return [0.82 for _ in pairs]


def test_retrieval_agent_canonical_prompt_and_ssot_registration_e2e(tmp_path: Path) -> None:
    """Verify Retrieval Agent prompt specification, SSOT pointer registration, and launch adapter execution."""
    assert "retrieval_surface_assurance" in TASK_SIDECAR_MAP
    assert "retrieval_surface_assurance" in EXPECTED_CANONICAL_PROMPTS
    assert "retrieval_surface_assurance" in TWELVE_GOVERNED_TASK_IDS

    info = TASK_SIDECAR_MAP["retrieval_surface_assurance"]
    assert info["sidecar_dir"] == "retrieval-surface-assurance-agent"
    assert info["canonical_prompt"] == "automations/prompts/retrieval_surface_agent.md"
    assert info["title"] == "Retrieval Surface Agent"

    tdef = get_task_definition("retrieval_surface_assurance")
    assert tdef["prompt_path"] == "automations/prompts/retrieval_surface_agent.md"
    assert tdef["registration_status"]["status"] == "RECONCILED_ACTIVE"

    pointer = generate_ssot_pointer_prompt("retrieval_surface_assurance", REPO_ROOT)
    assert is_valid_pointer_prompt(pointer)

    prompt_path = REPO_ROOT / info["canonical_prompt"]
    assert prompt_path.is_file()
    content = prompt_path.read_text(encoding="utf-8")
    assert "Canonical Task: retrieval_surface_assurance" in content
    assert "INV-RETRIEVAL-GAPS-FIRST-GATE" in content
    assert "INV-RETRIEVAL-ROI-ENHANCEMENT-GATE" in content
    assert "UNCONDITIONAL_WAVE_PLAN_MANDATE" in content
    assert "INV-RUNTIME-PROOF-5POINT" in content

    ok, errors = run_prompt_ssot_lint(REPO_ROOT)
    assert ok, f"Prompt SSOT lint failed: {errors}"

    adapter = AntigravityHostLaunchAdapter(repo_root=REPO_ROOT)
    res_clean = adapter.launch_task("retrieval_surface_assurance", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_clean.terminal_outcome == "COMPLETE"

    (tmp_path / "retrieval_surface_gap.json").write_text("{}", encoding="utf-8")
    res_gap = adapter.launch_task("retrieval_surface_assurance", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_gap.terminal_outcome == "HITL_REQUIRED"
    assert res_gap.implementation_plan_path is not None
    assert Path(res_gap.implementation_plan_path).is_file()


def test_end_to_end_seven_surface_retrieval_pipeline_and_release_qualification() -> None:
    """Verify end-to-end execution across RetrievalCoordinator, PTCHybridQueryExecutor, BgeRerankerService, and SDK tools."""
    # 1. Surface 2: RetrievalCoordinator perspective ranking + starvation healer + strict 11-gate release audit
    strict_auditor = ReleaseGateAuditor(mock_mode=True, strict_payload=True)
    coordinator = RetrievalCoordinator(mock_mode=True, auditor_agent=strict_auditor)
    candidates = [
        {
            "node_id": "node-tech-e2e",
            "name": "BGE-M3 Reranker Architecture",
            "content": "Technical architecture system contract for sidecar vector SQLite hybrid search.",
            "confidence": 0.96,
            "classification": "CLIENT_SAFE",
            "is_quarantined": 0,
        },
        {
            "node_id": "node-comm-e2e",
            "name": "Enterprise SOW Milestone",
            "content": "Commercial milestone SOW commitment delivery date and budget.",
            "confidence": 0.93,
            "classification": "CLIENT_SAFE",
            "is_quarantined": 0,
        },
        {
            "node_id": "node-adopt-e2e",
            "name": "Copilot Onboarding Workflow",
            "content": "Adoption friction stakeholder training onboarding and change readiness.",
            "confidence": 0.91,
            "classification": "CLIENT_SAFE",
            "is_quarantined": 0,
        },
    ]
    res = coordinator.retrieve(
        query="LENS multi-perspective architecture, commercial milestone, adoption, and blocker analysis",
        candidate_facts=candidates,
        caller_namespace="CLIENT_SAFE",
        stealth_mode=True,
    )
    assert res["all_gates_passed"] is True
    assert res["qualification_status"] == "DELIVERY_READY"
    assert "BLOCKER" in res["healed_perspectives"]

    # 2. Surface 5: PTCHybridQueryExecutor with RetrievedFact objects, role filtering, and subgraph traversal
    records: List[Any] = [
        RetrievedFact(
            node_id="node-tech-e2e",
            evidence_id="ev-e2e-1",
            namespace="CLIENT_SAFE",
            type="Architecture",
            name="BGE-M3 Reranker Architecture",
            snippet="Sub-second hybrid query routing",
            strategic_value=0.92,
            confidence=0.96,
            tacticality=0.85,
            is_quarantined=False,
            relevance_score=0.94,
            properties={"role": "staff_engineer"},
            connected_edges=[{"from_node_id": "node-tech-e2e", "to_node_id": "node-vec-1", "relationship": "QUERIES"}],
        ),
        {
            "node_id": "node-unauth-e2e",
            "name": "Restricted Draft",
            "type": "Draft",
            "snippet": "Excluded role item",
            "strategic_value": 0.5,
            "confidence": 0.6,
            "relevance_score": 0.5,
            "evidence_id": "ev-e2e-2",
            "role": "external_guest",
            "connected_edges": [],
        },
    ]
    executor = PTCHybridQueryExecutor(_E2EHybridSearchEngine(records))
    plan = PTCHybridQueryPlan(
        query="BGE-M3 Reranker",
        expand_subgraph=True,
        max_subgraph_depth=1,
        allowed_roles=["staff_engineer"],
    )
    pack = executor.execute(plan)
    assert len(pack.top_facts) == 1
    assert pack.top_facts[0]["node_id"] == "node-tech-e2e"
    assert len(pack.subgraph_edges) == 1

    # 3. Surface 5 & 7: 3-modality BGE-M3 hybrid scoring and empirical 11-gate candidate evaluation
    fused_score = compute_hybrid_scoring(0.92, 0.85, colbert_score=0.95)
    assert fused_score == round(0.92 * 0.50 + 0.85 * 0.30 + 0.95 * 0.20, 4)

    gate_eval = evaluate_candidate_retrieval(
        query="BGE-M3 Reranker",
        top_k=5,
        candidates=[
            {"id": "c1", "relevant": True, "score": fused_score, "forbidden_leakage": False},
            {"id": "c2", "relevant": True, "score": 0.88, "forbidden_leakage": False},
        ],
    )
    assert gate_eval["passed_11_gates"] is True
    assert gate_eval["status"] == "QUALIFIED"

    # 4. Surface 1: BgeRerankerService bounded LRU eviction preserves 10,000 hot entries
    _PAIR_SCORE_CACHE.clear()
    model_name = "e2e-bounded-lru-reranker"
    _RERANKER_MODEL_CACHE[(model_name, False)] = _E2ERerankerBackend()
    try:
        svc = BgeRerankerService(model_name=model_name, use_fp16=False)
        for idx in range(_MAX_PAIR_CACHE_ENTRIES):
            _PAIR_SCORE_CACHE[(model_name, "e2e_q", f"e2e_doc_{idx}")] = 0.5
        svc.predict([("e2e_q", "e2e_doc_overflow")])
        assert len(_PAIR_SCORE_CACHE) == _MAX_PAIR_CACHE_ENTRIES
        assert (model_name, "e2e_q", "e2e_doc_0") not in _PAIR_SCORE_CACHE
        assert (model_name, "e2e_q", "e2e_doc_1") in _PAIR_SCORE_CACHE
    finally:
        _PAIR_SCORE_CACHE.clear()
        _RERANKER_MODEL_CACHE.pop((model_name, False), None)


def test_wave1_retrieval_surface_functionality_gaps_e2e() -> None:
    """E2E verification of GAP-01 (L1 clearance & healer rescoring), GAP-02 (PTC role/depth/neighbors), and GAP-03 (copilot multi-query acronym expansion)."""
    import pytest
    from dual_pipeline.live_meeting_recorder_core.copilot_retriever_ranking import collect_multi_query_rankings
    from pipeline_c_retrieval.engine import PolicyGatedRetrievalEngine

    coord = RetrievalCoordinator(mock_mode=True)
    # GAP-01(a): Non-stealth INTERNAL_ADVISORY query must fail closed at L1 clearance check
    with pytest.raises(ValueError, match="NON_STEALTH_ADVISORY_QUERY_REJECTED"):
        coord.retrieve(
            query="executive leadership transition",
            candidate_facts=[],
            caller_namespace="INTERNAL_ADVISORY",
            stealth_mode=False,
        )

    from dual_pipeline.retrieval.agents.retrieval_perspective_healer import get_retrieval_perspective_healer_card

    # GAP-01(b): Zero-hit perspective lane uses RetrievalPerspectiveHealer broadened query to re-score facts
    class _E2EHealer:
        card = get_retrieval_perspective_healer_card(use_local_model=True)

        async def handle_task(self, task):
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "healed_artifact": {"query": "remediated risk mitigation plan", "starved_lens": task.input_payload.get("starved_lens", "TECHNICAL")},
            }

    coord_heal = RetrievalCoordinator(mock_mode=True, healer_agent=_E2EHealer())
    res_healed = coord_heal.retrieve(
        query="xyzunmatchedtoken",
        candidate_facts=[
            {
                "node_id": "fact-low-relevant",
                "name": "Risk Mitigation Plan",
                "content": "remediated risk mitigation plan",
                "confidence": 0.40,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
            {
                "node_id": "fact-high-irrelevant",
                "name": "Catering Invoice",
                "content": "completely unrelated catering invoice",
                "confidence": 0.95,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
        ],
        caller_namespace="CLIENT_SAFE",
        stealth_mode=False,
    )
    assert res_healed["ranked_results"][0]["node_id"] == "fact-low-relevant"

    # GAP-02: PolicyGatedRetrievalEngine.execute_programmatic_query threads allowed_roles & max_subgraph_depth,
    # filters out empty/None roles when allowed_roles is non-empty, bounds edges by max_subgraph_depth, and populates neighbor nodes
    class _StubPolicyEngine(PolicyGatedRetrievalEngine):
        def __init__(self, items):
            self._items = items

        def search(self, **kwargs):
            return list(self._items)

    engine = _StubPolicyEngine(
        [
            {
                "node_id": "node-auth-seed",
                "name": "Authorized Seed",
                "type": "Decision",
                "snippet": "Approved architecture",
                "strategic_value": 0.9,
                "confidence": 0.9,
                "relevance_score": 0.9,
                "evidence_id": "ev-auth",
                "role": "principal_architect",
                "connected_edges": [
                    {"from_node_id": "node-auth-seed", "to_node_id": "node-neighbor-1", "relationship": "GOVERNED_BY", "hop": 1},
                    {"from_node_id": "node-neighbor-1", "to_node_id": "node-hop-3", "relationship": "DEPENDS_ON", "hop": 3},
                ],
            },
            {
                "node_id": "node-empty-role",
                "name": "Unlabeled Role Fact",
                "type": "Claim",
                "snippet": "Should be blocked when allowed_roles is specified",
                "strategic_value": 0.99,
                "confidence": 0.99,
                "relevance_score": 0.99,
                "evidence_id": "ev-empty",
                "role": "",
                "connected_edges": [],
            },
        ]
    )
    ptc_res = engine.execute_programmatic_query(
        query="Approved architecture",
        top_k=5,
        expand_subgraph=True,
        max_subgraph_depth=1,
        allowed_roles=["principal_architect"],
    )
    assert [f["node_id"] for f in ptc_res["top_facts"]] == ["node-auth-seed"]
    assert len(ptc_res["subgraph_edges"]) == 1
    assert ptc_res["subgraph_edges"][0]["to_node_id"] == "node-neighbor-1"
    subgraph_node_ids = {n["node_id"] for n in ptc_res["subgraph_nodes"]}
    assert "node-auth-seed" in subgraph_node_ids
    assert "node-neighbor-1" in subgraph_node_ids

    # GAP-03: Secondary "ask" and "topic" queries in collect_multi_query_rankings expand domain acronyms
    captured_tokens = {}

    class _FakeCopilotRetriever:
        def _retrieve_lexical_ranking(self, q_toks, seeds, q_text, priority_only, max_results):
            captured_tokens[q_text] = set(q_toks)
            return [("rec-1", 1.0)]

    collect_multi_query_rankings(
        retriever=_FakeCopilotRetriever(),
        multi_queries={
            "primary": "general update",
            "ask": "what is the scada timeline?",
            "topic": "developer productivity metrics",
        },
        query_tokens={"general", "update"},
        direct_seed_nodes=set(),
        priority_only=False,
        max_results=5,
    )
    assert "historian" in captured_tokens["what is the scada timeline?"]
    assert "velocity" in captured_tokens["developer productivity metrics"]


def test_wave2_retrieval_surface_high_roi_enhancements_e2e(tmp_path) -> None:
    """E2E verification of ENH-01 (Copilot MMR hybrid dense+lexical similarity) and ENH-02 (ColBERT & QueryEncodingCache LRU)."""
    import numpy as np
    from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache
    from dual_pipeline.live_meeting_recorder_core.copilot_retrieval_fusion import maximal_marginal_relevance
    from dual_pipeline.retrieval_release_core.query_encoding_cache import QueryEncodingCache

    # ENH-01: Paraphrased duplicate with zero lexical overlap is demoted by MMR when dense_matrix shows cosine=1.0
    candidate_items = [
        {"assertion_id": "a-lead", "claim_text": "transformer thermal overload risk"},
        {"assertion_id": "a-paraphrase", "claim_text": "grid hardware overheating hazard"},
        {"assertion_id": "a-distinct", "claim_text": "quarterly capital budget schedule"},
    ]
    candidate_scores = {"a-lead": 1.0, "a-paraphrase": 0.95, "a-distinct": 0.85}
    dense_matrix = np.array(
        [
            [1.0, 0.0],
            [1.0, 0.0],  # Identical embedding to a-lead
            [0.0, 1.0],  # Orthogonal distinct topic
        ],
        dtype=np.float32,
    )
    idx_map = {"a-lead": 0, "a-paraphrase": 1, "a-distinct": 2}
    selected = maximal_marginal_relevance(
        candidate_items, candidate_scores, max_results=2, lambda_param=0.5, dense_matrix=dense_matrix, record_id_to_idx=idx_map
    )
    assert [item["assertion_id"] for item in selected] == ["a-lead", "a-distinct"]

    # ENH-02: PrePaddedColbertCache LRU promotion and QueryEncodingCache bounded in-memory LRU
    colbert_cache = PrePaddedColbertCache(max_entries=2, max_bytes=1024 * 1024)
    tensor = np.ones((2, 4), dtype=np.float32)
    lengths = np.array([2, 2], dtype=np.int32)
    colbert_cache.set(("batch-1",), tensor, lengths, None)
    colbert_cache.set(("batch-2",), tensor, lengths, None)
    assert colbert_cache.get(("batch-1",)) is not None
    colbert_cache.set(("batch-3",), tensor, lengths, None)
    assert colbert_cache.get(("batch-1",)) is not None
    assert colbert_cache.get(("batch-2",)) is None

    class _E2EEncodingBackend:
        backend_id = "e2e-bge"
        model_name = "e2e-model"

    q_cache = QueryEncodingCache(tmp_path / "e2e_query_cache.sqlite", max_entries=2)
    backend = _E2EEncodingBackend()
    q_cache.set(backend, "query-1", {"dense": [0.1, 0.2]})
    q_cache.set(backend, "query-2", {"dense": [0.3, 0.4]})
    assert q_cache.get(backend, "query-1") == {"dense": [0.1, 0.2]}
    q_cache.set(backend, "query-3", {"dense": [0.5, 0.6]})
    assert len(q_cache._entries) == 2
    # Even if evicted from in-memory _entries, SQLite readback re-hydrates query-2 seamlessly
    assert q_cache.get(backend, "query-2") == {"dense": [0.3, 0.4]}
    assert len(q_cache._entries) == 2


def test_wave1_general_starvation_rrf_filter_copilot_dense_id_and_batch_lru_e2e(tmp_path) -> None:
    """Counterfactual E2E test for GAP-01 (GENERAL starvation & zero-score RRF filtering), GAP-02 (Copilot _extract_query_tokens & _load_dense_vectors assertion_id mapping), and GAP-03 (QueryEncodingCache set_many/_load LRU bound)."""
    import json
    import numpy as np
    from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
        CopilotGraphRetriever,
        _extract_query_tokens,
    )
    from dual_pipeline.retrieval.agents.retrieval_perspective_healer import get_retrieval_perspective_healer_card
    from dual_pipeline.retrieval_release_core.query_encoding_cache import QueryEncodingCache
    from storage.connection import create_sqlite_conn

    # 1. GAP-01: GENERAL perspective query with 0 token hits triggers starvation healer and filters 0-score facts from RRF
    from dual_pipeline.retrieval.agents.multi_perspective_dispatcher import get_multi_perspective_dispatcher_card

    class _GeneralDispatcher:
        card = get_multi_perspective_dispatcher_card(use_local_model=True)

        async def handle_task(self, task):
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "sub_queries": [{"perspective": "GENERAL", "query_text": task.input_payload.get("query", ""), "weight": 1.0}],
            }

    class _GeneralHealer:
        card = get_retrieval_perspective_healer_card(use_local_model=True)

        async def handle_task(self, task):
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "healed_artifact": {"query": "grid substation telemetry", "starved_lens": task.input_payload.get("starved_lens", "GENERAL")},
            }

    coord = RetrievalCoordinator(mock_mode=True, healer_agent=_GeneralHealer())
    coord.dispatcher_agent = _GeneralDispatcher()
    coord.broker.register_agent(coord.dispatcher_agent.card, coord.dispatcher_agent.handle_task)
    res = coord.retrieve(
        query="unmatchedgeneralquery",
        candidate_facts=[
            {
                "node_id": "fact-general-match",
                "name": "Grid Substation Telemetry",
                "content": "grid substation telemetry sensor readings",
                "confidence": 0.40,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
            {
                "node_id": "fact-general-irrelevant",
                "name": "Office Supplies",
                "content": "completely unrelated stationery order",
                "confidence": 0.99,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
        ],
        caller_namespace="CLIENT_SAFE",
        stealth_mode=False,
    )
    assert "GENERAL" in res["healed_perspectives"]
    assert res["ranked_results"][0]["node_id"] == "fact-general-match"

    # 2. GAP-02: _extract_query_tokens expands topic/role synonyms and _load_dense_vectors maps assertion_id (including com.partner_advisory.nee.graph-object-id) in _record_id_to_idx
    expanded_toks = _extract_query_tokens("developer productivity and tech owner")
    assert "velocity" in expanded_toks
    assert "lead" in expanded_toks

    sidecar_db = tmp_path / "copilot_sidecar.sqlite"
    con = create_sqlite_conn(sidecar_db)
    con.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, vector BLOB)")
    con.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, artifact_json TEXT)")
    vec0 = np.array([1.0, 0.0], dtype=np.float32).tobytes()
    vec1 = np.array([0.0, 1.0], dtype=np.float32).tobytes()
    con.execute("INSERT INTO dense_vector VALUES (?, ?), (?, ?), (?, ?)", ("rec-0", vec0, "rec-1", vec1, "rec-2", vec0))
    con.execute(
        "INSERT INTO retrieval_record VALUES (?, ?), (?, ?)",
        (
            "rec-0",
            json.dumps({"assertion_id": "asrt-direct-0"}),
            "rec-1",
            json.dumps({"extensions": {"com.partner_advisory.nee.graph-object-id": "asrt-ext-1"}}),
        ),
    )
    con.commit()
    con.close()

    retriever = CopilotGraphRetriever.__new__(CopilotGraphRetriever)
    retriever._dense_record_to_assertion_id = {}
    retriever._dense_record_ids = []
    retriever._record_id_to_idx = {}
    retriever._dense_matrix = None
    retriever._load_dense_vectors(sidecar_db)
    assert retriever._record_id_to_idx.get("asrt-direct-0") == 0
    assert retriever._record_id_to_idx.get("asrt-ext-1") == 1
    assert retriever._dense_record_to_assertion_id.get("rec-2") == "rec-2"

    # 3. GAP-03: QueryEncodingCache.set_many() and _load() enforce max_entries LRU bound
    class _BatchBackend:
        backend_id = "batch-bge"
        model_name = "batch-model"

    cache_path = tmp_path / "batch_query_cache.sqlite"
    q_cache = QueryEncodingCache(cache_path, max_entries=2)
    backend = _BatchBackend()
    q_cache.set_many(backend, [("q1", {"d": [1]}), ("q2", {"d": [2]}), ("q3", {"d": [3]})])
    assert len(q_cache._entries) == 2

    reloaded = QueryEncodingCache(cache_path, max_entries=2)
    assert len(reloaded._entries) == 2


def test_wave2_vector_cache_record_id_set_fastpath_and_session_context_recency_e2e(tmp_path) -> None:
    """Counterfactual E2E test for ENH-01 (LaneDenseVectors.record_id_set C-level subset fastpath) and ENH-02 (search_session_context synonym expansion & recency tie-breaking)."""
    import numpy as np
    from dual_pipeline.index_core.vector_cache import VectorCache
    from dual_pipeline.live_meeting_recorder_core.copilot_retriever import search_session_context
    from storage.connection import create_sqlite_conn

    # 1. ENH-01: VectorCache populates LaneDenseVectors.record_id_set and scores full-lane superset authorized_record_ids
    sidecar_db = tmp_path / "vector_cache_sidecar.sqlite"
    con = create_sqlite_conn(sidecar_db)
    con.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT)")
    con.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, vector BLOB)")
    v1 = np.array([1.0, 0.0], dtype=np.float32).tobytes()
    v2 = np.array([0.0, 1.0], dtype=np.float32).tobytes()
    con.execute("INSERT INTO retrieval_record VALUES ('r1', 'assertion'), ('r2', 'assertion')")
    con.execute("INSERT INTO dense_vector VALUES ('r1', ?), ('r2', ?)", (v1, v2))
    con.commit()
    con.close()

    vc = VectorCache()
    lane_data = vc.get_or_load_lane(sidecar_db, "assertion", generation_id="gen-e2e")
    assert hasattr(lane_data, "record_id_set")
    assert lane_data.record_id_set == frozenset({"r1", "r2"})

    scores = vc.score_dense(
        sidecar_db,
        "assertion",
        np.array([1.0, 0.0], dtype=np.float32),
        authorized_record_ids={"r1", "r2", "r3"},
        generation_id="gen-e2e",
    )
    assert scores[0][1] == "r1"

    # 2. ENH-02: search_session_context expands acronyms/synonyms and tie-breaks equal overlap in favor of more recent turns
    turns = [
        {"timestamp": "10:01", "speaker": "Alice", "text": "Our historian ems pipeline is delayed"},
        {"timestamp": "10:05", "speaker": "Bob", "text": "The historian ems dashboard needs credentials"},
        {"timestamp": "10:10", "speaker": "Charlie", "text": "Recent turn 1"},
        {"timestamp": "10:11", "speaker": "Charlie", "text": "Recent turn 2"},
        {"timestamp": "10:12", "speaker": "Charlie", "text": "Recent turn 3"},
        {"timestamp": "10:13", "speaker": "Charlie", "text": "Recent turn 4"},
    ]
    # Query uses "scada" (expands to "historian", "ems", etc.); both 10:01 and 10:05 match 2 expanded tokens ("historian", "ems")
    # Recency tie-break must rank 10:05 Bob ahead of 10:01 Alice when top_k=1
    ctx = search_session_context(turns, "scada status", top_k=1)
    assert "10:05 Bob" in ctx




