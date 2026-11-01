"""Pipeline C (Retrieval) Layer 4: Policy-Gated Retrieval Engine.

Enforces:
  - Law 6: Policy Before Retrieval (Auth & Visibility Scope filtering BEFORE Vector / Hybrid search)
  - Law 2: Governed INTERNAL_ADVISORY Namespace
  - Law 5: Multi-Dimensional Soft-Quarantine Enforcement (is_quarantined = 0 in public view)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pipeline_c_retrieval.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    RetrievedFact,
)
from storage.database import (
    CanonicalGraphNode,
    DatabaseManager,
    InternalAdvisoryNode,
)

# Optional OpenTelemetry tracing integration
try:
    from dual_pipeline.telemetry import (
        format_traceparent,
        get_tracer,
        record_bipartite_leak_event,
    )
    tracer = get_tracer("pipeline_c_retrieval.engine")
except Exception:
    tracer = None

logger = logging.getLogger("pipeline_c_retrieval.engine")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [RETRIEVAL-ENGINE] %(message)s"
    ))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class PolicyGatedRetrievalEngine:
    """
    Retrieval engine enforcing Law 6:
      Hard SQL/Policy Filter executed BEFORE any semantic ranking.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        hybrid_retriever: Optional[Any] = None,
        vector_cache: Optional[Any] = None,
    ):
        self.db_manager = db_manager
        self.hybrid_retriever = hybrid_retriever
        self.vector_cache = vector_cache

    def search(
        self, query: str, stealth_mode: bool = True, min_strategic_value: float = 0.0,
        include_quarantined: bool = False, limit: int = 10, max_hops: int = 1,
    ) -> List[RetrievedFact]:
        """
        Executes policy-gated hybrid retrieval:
          1. Apply Auth/Visibility WHERE clauses (is_quarantined=0, namespace=CLIENT_SAFE).
          2. Execute BM25 / token similarity scoring strictly on the pre-filtered candidates.
          3. Enforce leak detection: If stealth_mode=True, throw security exception if advisory leaks.
        """
        span_ctx = None
        if tracer:
            span_ctx = tracer.start_as_current_span("pipeline.c.retrieval_query")
            span = span_ctx.__enter__()
            span.set_attribute("retrieval.query", query or "*")
            span.set_attribute("retrieval.stealth_mode", stealth_mode)
            span.set_attribute("retrieval.min_strategic_value", min_strategic_value)

        try:
            # ── STEP 1: Policy Filter on Canonical Graph (SQL Level) ──────
            canonical_nodes = self.db_manager.query_canonical_nodes(
                min_strategic_value=min_strategic_value,
                include_quarantined=include_quarantined,
                limit=max(limit * 2, 20),
            )

            # ── STEP 1b: High-Performance SQLite FTS5 Full-Text Search ─────
            fts_hits: List[CanonicalGraphNode] = []
            if query.strip():
                fts_hits = self.db_manager.search_canonical_nodes_fts(
                    query_text=query,
                    limit=limit * 3,
                    include_quarantined=include_quarantined,
                )
            fts_hit_ids = {n.node_id: rank for rank, n in enumerate(fts_hits)}

            # Combine node pool (canonical base + FTS hits)
            node_pool_dict = {n.node_id: n for n in canonical_nodes}
            for fn in fts_hits:
                if fn.node_id not in node_pool_dict:
                    node_pool_dict[fn.node_id] = fn
            all_canonical_pool = list(node_pool_dict.values())

            # ── STEP 2: Policy Filter on Internal Advisory (if unmasked) ──
            advisory_nodes: List[InternalAdvisoryNode] = []
            if not stealth_mode:
                advisory_nodes = self.db_manager.query_internal_advisory_nodes(
                    include_quarantined=include_quarantined,
                    limit=max(limit * 2, 20),
                )

            # ── STEP 3: Quad/Hybrid Scoring with Reciprocal Rank Fusion ───
            query_tokens = set(re.findall(r"\w+", query.lower())) if query.strip() else set()
            scored_results: List[RetrievedFact] = []

            # Compute vector dense scores for authorized candidates if hybrid retriever attached
            hybrid_dense_scores: Dict[str, float] = {}
            if self.hybrid_retriever is not None and query.strip() and all_canonical_pool:
                try:
                    auth_node_ids = {n.node_id for n in all_canonical_pool}
                    cache = self.vector_cache or getattr(self.hybrid_retriever, "vector_cache", None)
                    if cache and hasattr(self.hybrid_retriever, "sidecar_path"):
                        enc = self.hybrid_retriever.backend.encode(query)
                        raw_scores = cache.score_dense(
                            sidecar_path=self.hybrid_retriever.sidecar_path,
                            lane="approved",
                            query_vector=enc["dense"],
                            authorized_record_ids=auth_node_ids,
                            generation_id=getattr(self.hybrid_retriever, "validation", {}).get("metadata", {}).get("generation_id"),
                        )
                        hybrid_dense_scores = {rid: score for score, rid in raw_scores}
                except Exception as ex:
                    logger.debug(f"[RETRIEVAL-ENGINE] Hybrid vector scoring fallback: {ex}")

            # Score canonical items using RRF (FTS5 BM25 + Token Overlap + Vector Dense)
            for node in all_canonical_pool:
                token_score = self._compute_relevance(query_tokens, node.name, node.properties)
                dense_comp = max(0.0, hybrid_dense_scores.get(node.node_id, 0.0))

                # RRF calculation: FTS rank component + Token overlap component + Dense component
                if node.node_id in fts_hit_ids:
                    fts_rank = fts_hit_ids[node.node_id]
                    rrf_score = (0.5 / (60 + fts_rank)) + (0.3 * token_score) + (0.2 * dense_comp)
                elif query_tokens:
                    if token_score <= 0.0 and dense_comp <= 0.0:
                        continue
                    rrf_score = (token_score * 0.3) + (0.3 * dense_comp)
                else:
                    rrf_score = max(0.1, node.strategic_value / 10.0)


                ctx = str(node.properties.get("context", ""))
                snippet = ctx[:160] + "..." if len(ctx) > 160 else (ctx or node.name)

                scored_results.append(RetrievedFact(
                    node_id=node.node_id,
                    evidence_id=node.evidence_id,
                    namespace=getattr(node, "namespace", CLIENT_SAFE_NAMESPACE) or CLIENT_SAFE_NAMESPACE,
                    type=node.type,
                    name=node.name,
                    snippet=snippet,
                    strategic_value=node.strategic_value,
                    confidence=node.confidence,
                    tacticality=node.tacticality,
                    is_quarantined=node.is_quarantined,
                    relevance_score=round(rrf_score, 4),
                    properties=node.properties,
                    connected_edges=[],
                ))

            # Score advisory items (only when stealth_mode is False)
            for adv in advisory_nodes:
                token_score = self._compute_relevance(query_tokens, adv.name, adv.properties)
                if query_tokens and token_score <= 0.0:
                    continue

                signal = str(adv.properties.get("signal", adv.properties.get("rationale", "")))
                snippet = signal[:160] + "..." if len(signal) > 160 else (signal or adv.name)

                scored_results.append(RetrievedFact(
                    node_id=adv.node_id,
                    evidence_id=adv.evidence_id,
                    namespace=INTERNAL_ADVISORY_NAMESPACE,
                    type=adv.category.value if hasattr(adv.category, "value") else str(adv.category),
                    name=adv.name,
                    snippet=snippet,
                    strategic_value=adv.strategic_value,
                    confidence=adv.confidence,
                    tacticality=adv.tacticality,
                    is_quarantined=adv.is_quarantined,
                    relevance_score=round(token_score, 4),
                    properties=adv.properties,
                    connected_edges=[],
                ))

            # ── STEP 4: Zero-Leakage Security Assertion (Law 6) ───────────
            if stealth_mode:
                for item in scored_results:
                    if item.namespace == INTERNAL_ADVISORY_NAMESPACE:
                        if tracer and span_ctx:
                            record_bipartite_leak_event(
                                span,
                                requested_namespace=CLIENT_SAFE_NAMESPACE,
                                leaked_node_id=item.node_id,
                            )
                        raise PermissionError(
                            "CRITICAL SECURITY VIOLATION: INTERNAL_ADVISORY leaked in Stealth Mode!"
                        )

            # Sort by relevance score, then by strategic value
            scored_results.sort(key=lambda r: (r.relevance_score, r.strategic_value), reverse=True)
            top_candidates = scored_results[:limit]

            # ── STEP 5: Multi-Hop Graph Traversal & Edge Hydration ────────
            top_canonical_ids = [c.node_id for c in top_candidates if c.namespace == CLIENT_SAFE_NAMESPACE]
            if top_canonical_ids:
                try:
                    _, neighborhood_edges = self.db_manager.traverse_canonical_neighborhood(
                        seed_node_ids=top_canonical_ids,
                        max_hops=max(1, int(max_hops)),
                        include_quarantined=include_quarantined,
                    )
                    edge_map: Dict[str, List[Dict[str, Any]]] = {}
                    for e in neighborhood_edges:
                        edge_dict = e.to_dict()
                        edge_map.setdefault(e.from_node_id, []).append(edge_dict)
                        edge_map.setdefault(e.to_node_id, []).append(edge_dict)

                    for fact in top_candidates:
                        if fact.node_id in edge_map:
                            fact.connected_edges = edge_map[fact.node_id]
                except Exception as e:
                    logger.debug(f"Multi-hop graph edge hydration bypassed: {e}")

            final_results = top_candidates

            if tracer and span_ctx:
                span.set_attribute("retrieval.result_count", len(final_results))
                span.set_attribute("retrieval.policy_passed", True)
                tp = format_traceparent(span)
                for r in final_results:
                    r.traceparent = tp

            return final_results
        finally:
            if span_ctx:
                span_ctx.__exit__(None, None, None)

    def _compute_relevance(
        self,
        query_tokens: set[str],
        name: str,
        properties: Dict[str, Any],
    ) -> float:
        """Computes keyword match overlap ratio."""
        if not query_tokens:
            return 1.0
        doc_text = f"{name} {json.dumps(properties)}".lower()
        matches = sum(1 for token in query_tokens if token in doc_text)
        return matches / len(query_tokens)

    def execute_programmatic_query(
        self,
        query: str,
        stealth_mode: bool = True,
        min_strategic_value: float = 0.0,
        include_quarantined: bool = False,
        limit: int = 5,
        expand_subgraph: bool = True,
        max_subgraph_depth: int = 1,
        allowed_roles: Optional[List[str]] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Programmatic entrypoint for Single-Turn Hybrid Retrieval & Evidence Pack Assembly."""
        from dual_pipeline.ptc.hybrid_query import (
            PTCHybridQueryExecutor,
            PTCHybridQueryPlan,
        )
        eff_limit = int(top_k) if top_k is not None else limit
        plan = PTCHybridQueryPlan(
            query=query,
            stealth_mode=stealth_mode,
            min_strategic_value=min_strategic_value,
            include_quarantined=include_quarantined,
            limit=eff_limit,
            expand_subgraph=expand_subgraph,
            max_subgraph_depth=max_subgraph_depth,
            allowed_roles=allowed_roles,
        )
        executor = PTCHybridQueryExecutor(self)
        pack = executor.execute(plan)
        return pack.to_dict()
