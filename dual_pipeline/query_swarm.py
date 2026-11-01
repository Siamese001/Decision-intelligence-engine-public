"""Competitive Multi-Agent Query Routing Swarm with Semantic Intent Reasoning.

Upgrades query routing from keyword heuristics to structured semantic intent analysis
and competitive multi-agent evaluation across 8 specialized query routes.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import asdict, dataclass, field
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger("dual_pipeline.query_swarm")

QUERY_ROUTES: List[str] = [
    "ROUTE_1_EXACT_MATCH",
    "ROUTE_2_SEMANTIC_SEARCH",
    "ROUTE_3_GRAPH_TRAVERSAL",
    "ROUTE_4_HYBRID_BM25",
    "ROUTE_5_TEMPORAL_W3",
    "ROUTE_6_STRATEGIC_W6",
    "ROUTE_7_ANALYTICAL_AGGREGATION",
    "ROUTE_8_FALLBACK_LLM",
]

DEFAULT_ROUTE_MULTIPLIERS = {
    "relational": 0.45,
    "temporal": 0.45,
    "strategic": 0.45,
    "analytical": 0.45,
    "concept": 0.40,
}


def get_learned_route_multipliers(priors_path: Optional[Any] = None) -> Dict[str, float]:
    """Loads learned route multipliers derived from answer recall telemetry.

    Clamps multipliers strictly within bounded range [0.20, 0.70] preserving deterministic fallbacks.
    """
    multipliers = dict(DEFAULT_ROUTE_MULTIPLIERS)
    if priors_path:
        p_path = Path(priors_path).resolve()
        if p_path.is_file():
            try:
                import json
                data = json.loads(p_path.read_text(encoding="utf-8"))
                priors = data.get("learned_query_route_priors", data.get("learned_priors", {}).get("query_routing", {}))
                for k, v in priors.items():
                    if k in multipliers and isinstance(v, (int, float)):
                        multipliers[k] = max(0.20, min(0.70, float(v)))
            except Exception:
                pass
    return multipliers


@dataclass(frozen=True)
class QuerySemanticProfile:
    """Semantic intent profile decomposed from query structure and linguistics."""

    exact_quote_detected: bool
    relational_topology_score: float
    temporal_chronology_score: float
    strategic_evaluation_score: float
    analytical_metric_score: float
    semantic_concept_score: float
    lexical_breadth_score: float
    token_count: int
    dominant_intent: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SemanticIntentClassifier:
    """Classifies semantic intent across 8 query dimensions using linguistic and syntactic features."""

    EXACT_PATTERN = re.compile(r'["\'].+?["\']|\bexact\s+match\b|\bid:\s*\S+', re.IGNORECASE)
    RELATIONAL_PATTERN = re.compile(r"\b(connect(?:ed|ion|s)?|relat(?:es|ed|ion|ionship)|path|network|node|edge|graph|dependenc(?:y|ies)|lineage|neighbor|travers(?:e|al))\b", re.IGNORECASE)
    TEMPORAL_PATTERN = re.compile(r"\b(when|date|timeline|before|after|history|recent|latest|chronolog(?:y|ical)|preceding|subsequent|duration|stale|timestamp)\b", re.IGNORECASE)
    STRATEGIC_PATTERN = re.compile(r"\b(strategic|decision|trade-?off|risk|leverage|priority|executive|governance|impact|outcome|recommendation|consequence)\b", re.IGNORECASE)
    ANALYTICAL_PATTERN = re.compile(r"\b(count|sum|average|avg|total|aggregate|distribution|metrics|how\s+many|percentage|ratio|frequency)\b", re.IGNORECASE)
    CONCEPT_PATTERN = re.compile(r"\b(how|why|concept|meaning|explain|architecture|rationale|mechanism|overview|understand|synthesize)\b", re.IGNORECASE)

    @classmethod
    def decompose(
        cls,
        query: str,
        multipliers: Optional[Mapping[str, float]] = None,
    ) -> QuerySemanticProfile:
        q = query.strip()
        tokens = [t for t in re.split(r"\s+", q.lower()) if t]
        token_count = len(tokens)

        exact = bool(cls.EXACT_PATTERN.search(q))
        rel_matches = len(cls.RELATIONAL_PATTERN.findall(q))
        temp_matches = len(cls.TEMPORAL_PATTERN.findall(q))
        strat_matches = len(cls.STRATEGIC_PATTERN.findall(q))
        anal_matches = len(cls.ANALYTICAL_PATTERN.findall(q))
        concept_matches = len(cls.CONCEPT_PATTERN.findall(q))

        # Normalized feature intensities with learned route multipliers [0.0, 1.0]
        mults = multipliers or DEFAULT_ROUTE_MULTIPLIERS
        rel_score = min(1.0, rel_matches * mults.get("relational", 0.45))
        temp_score = min(1.0, temp_matches * mults.get("temporal", 0.45))
        strat_score = min(1.0, strat_matches * mults.get("strategic", 0.45))
        anal_score = min(1.0, anal_matches * mults.get("analytical", 0.45))
        concept_score = min(1.0, concept_matches * mults.get("concept", 0.40))
        lex_score = min(1.0, 0.40 + (0.10 * min(5, token_count)))

        scores = {
            "ROUTE_1_EXACT_MATCH": 1.0 if exact else 0.05,
            "ROUTE_6_STRATEGIC_W6": strat_score,
            "ROUTE_3_GRAPH_TRAVERSAL": rel_score,
            "ROUTE_5_TEMPORAL_W3": temp_score,
            "ROUTE_7_ANALYTICAL_AGGREGATION": anal_score,
            "ROUTE_2_SEMANTIC_SEARCH": concept_score,
            "ROUTE_4_HYBRID_BM25": lex_score,
        }
        dominant = max(scores, key=lambda k: scores[k])

        return QuerySemanticProfile(
            exact_quote_detected=exact,
            relational_topology_score=round(rel_score, 3),
            temporal_chronology_score=round(temp_score, 3),
            strategic_evaluation_score=round(strat_score, 3),
            analytical_metric_score=round(anal_score, 3),
            semantic_concept_score=round(concept_score, 3),
            lexical_breadth_score=round(lex_score, 3),
            token_count=token_count,
            dominant_intent=dominant,
        )


_ROUTE_SCORE_CONFIG = {
    "ROUTE_6_STRATEGIC_W6": ("strategic_evaluation_score", 0.85, 0.10, 0.95, 0.15),
    "ROUTE_3_GRAPH_TRAVERSAL": ("relational_topology_score", 0.85, 0.10, 0.95, 0.12),
    "ROUTE_5_TEMPORAL_W3": ("temporal_chronology_score", 0.85, 0.10, 0.95, 0.12),
    "ROUTE_7_ANALYTICAL_AGGREGATION": ("analytical_metric_score", 0.85, 0.10, 0.95, 0.10),
}


def _calc_scored_intent(route_id: str, profile: QuerySemanticProfile) -> Optional[float]:
    cfg = _ROUTE_SCORE_CONFIG.get(route_id)
    if not cfg:
        return None
    attr, base, mult, cap, fallback = cfg
    score = getattr(profile, attr, 0.0)
    return min(cap, base + (score * mult)) if score >= 0.40 else fallback


def _calc_exact_match_score(profile: QuerySemanticProfile) -> float:
    if profile.exact_quote_detected:
        return 0.96
    return 0.60 if profile.token_count <= 2 else 0.10


def _calc_semantic_search_score(profile: QuerySemanticProfile) -> float:
    if profile.semantic_concept_score >= 0.40:
        return min(0.92, 0.80 + (profile.semantic_concept_score * 0.12))
    return 0.70 if profile.token_count > 4 else 0.15


def _calc_route_confidence(route_id: str, query: str, profile: QuerySemanticProfile) -> float:
    """Calculates route-specific confidence grounded in the semantic intent profile."""
    scored = _calc_scored_intent(route_id, profile)
    if scored is not None:
        return scored

    if route_id == "ROUTE_1_EXACT_MATCH":
        return _calc_exact_match_score(profile)

    if route_id == "ROUTE_2_SEMANTIC_SEARCH":
        return _calc_semantic_search_score(profile)

    if route_id == "ROUTE_4_HYBRID_BM25":
        return 0.85 if profile.token_count >= 3 else 0.70

    if route_id == "ROUTE_8_FALLBACK_LLM":
        return 0.25

    return 0.10


class QueryRouteSubAgent:
    """Dedicated specialist agent evaluating suitability for a specific query route."""

    def __init__(self, route_id: str):
        self.route_id = route_id

    def _apply_graph_density_probe(
        self, confidence: float, graph_stats: Optional[Mapping[str, Any]], refs: List[str]
    ) -> float:
        if not graph_stats:
            return confidence
        refs.append("graph_probe:density_verified")
        if self.route_id == "ROUTE_3_GRAPH_TRAVERSAL" and int(graph_stats.get("multi_hop_edges", 0)) > 0:
            return min(0.96, confidence + 0.06)
        if self.route_id == "ROUTE_1_EXACT_MATCH" and int(graph_stats.get("anchor_node_matches", 0)) > 0:
            return min(0.97, confidence + 0.05)
        if self.route_id == "ROUTE_5_TEMPORAL_W3" and int(graph_stats.get("temporal_snapshots", 0)) > 0:
            return min(0.96, confidence + 0.05)
        return confidence

    def evaluate_query(
        self,
        query: str,
        profile: Optional[QuerySemanticProfile] = None,
        graph_stats: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluates how well this route can handle the query based on semantic and graph probe characteristics."""
        q_clean = query.strip()
        if not q_clean:
            return {
                "route_id": self.route_id,
                "confidence": 0.0,
                "plan": f"Default fallback plan for {self.route_id}",
                "evidence_refs": [],
            }

        prof = profile or SemanticIntentClassifier.decompose(query)
        refs = [f"intent:{prof.dominant_intent}"]
        confidence = _calc_route_confidence(self.route_id, query, prof)
        confidence = self._apply_graph_density_probe(confidence, graph_stats, refs)
        plan_desc = f"Execute retrieval via {self.route_id} (Dominant intent: {prof.dominant_intent})"

        return {
            "route_id": self.route_id,
            "confidence": round(confidence, 2),
            "plan": plan_desc,
            "evidence_refs": refs,
        }


class CompetitiveQuerySwarm:
    """Manages the 8 competitive query routing specialist agents."""

    def __init__(
        self,
        priors_path: Optional[Any] = None,
        integrator: Optional[Any] = None,
        route_multipliers: Optional[Dict[str, float]] = None,
    ):
        self.priors_path = priors_path
        self.route_multipliers = route_multipliers or get_learned_route_multipliers(priors_path)
        self.agents = [QueryRouteSubAgent(route) for route in QUERY_ROUTES]
        if integrator is not None:
            self.integrator = integrator
        else:
            try:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import (
                    get_live_pipeline_integrator,
                )
                self.integrator = get_live_pipeline_integrator()
            except ImportError:
                self.integrator = None

    def route_query(
        self,
        query: str,
        graph_stats: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Decomposes semantic intent with learned multipliers, broadcasts to specialists concurrently, and selects winner."""
        start_time = time.time()
        profile = SemanticIntentClassifier.decompose(query, multipliers=self.route_multipliers)
        results: List[Dict[str, Any]] = []

        import contextvars
        from config.request_deadline import remaining_timeout
        t_budget = remaining_timeout(30.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.agents)) as executor:
            futures = [
                executor.submit(contextvars.copy_context().run, agent.evaluate_query, query, profile, graph_stats)
                for agent in self.agents
            ]
            for future in concurrent.futures.as_completed(futures, timeout=t_budget):
                results.append(future.result(timeout=t_budget))

        # Select the route with the highest confidence
        winner = max(results, key=lambda x: x["confidence"])
        latency_ms = round((time.time() - start_time) * 1000, 2)

        winner["latency_ms"] = latency_ms
        winner["semantic_profile"] = profile.to_dict()
        winner["candidate_rankings"] = sorted(
            [{"route_id": r["route_id"], "confidence": r["confidence"]} for r in results],
            key=lambda x: x["confidence"],
            reverse=True,
        )

        sorted_results = sorted(results, key=lambda x: x["confidence"], reverse=True)
        if len(sorted_results) >= 2:
            margin = round(sorted_results[0]["confidence"] - sorted_results[1]["confidence"], 2)
            thresh = get_hitl_ambiguity_margin_threshold()
            winner["margin"] = margin
            if margin <= thresh:
                logger.warning(f"Ambiguity margin {margin:.2f} <= {thresh:.2f}! Escalating to Orchestrator A2A mesh.")
                winner["escalation_required"] = True
                winner["fused_routes"] = [sorted_results[0]["route_id"], sorted_results[1]["route_id"]]
                winner["autonomy_decision"] = {
                    "outcome_type": "DUAL_ROUTE_FUSION_AND_ESCALATE",
                    "margin_delta": margin,
                    "top_routes": list(winner["fused_routes"]),
                }
            else:
                winner["escalation_required"] = False
                winner["fused_routes"] = [sorted_results[0]["route_id"]]
                winner["autonomy_decision"] = {
                    "outcome_type": "AUTONOMOUS",
                    "margin_delta": margin,
                    "top_routes": [sorted_results[0]["route_id"]],
                }

        return winner
