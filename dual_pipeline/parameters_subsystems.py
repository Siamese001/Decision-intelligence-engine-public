"""
dual_pipeline/parameters_subsystems.py

Centralized SSOT accessors for graph consolidation, graph retrieval, graph signal
scoring, hybrid index batching, document parser security, decision service,
authorization, agent architecture, and L0 assumption lifecycle parameters.
"""
from __future__ import annotations

from typing import Any, Dict


def get_graph_consolidation_parameters() -> Dict[str, float]:
    """Return SSOT similarity thresholds, confidence margin bounds, and modularity floor."""
    from .parameters import get_float_parameter
    return {
        "default_node_similarity_threshold": get_float_parameter(
            "graph_consolidation.default_node_similarity_threshold", 0.65
        ),
        "default_literal_similarity_threshold": get_float_parameter(
            "graph_consolidation.default_literal_similarity_threshold", 0.85
        ),
        "default_evidence_similarity_threshold": get_float_parameter(
            "graph_consolidation.default_evidence_similarity_threshold", 0.90
        ),
        "min_confidence_margin": get_float_parameter(
            "graph_consolidation.min_confidence_margin", 0.05
        ),
        "max_confidence_margin": get_float_parameter(
            "graph_consolidation.max_confidence_margin", 0.30
        ),
        "min_modularity": get_float_parameter(
            "graph_consolidation.min_modularity", 0.60
        ),
    }


def get_graph_retrieval_parameters() -> Dict[str, float]:
    """Return SSOT GS-10 temporal decay and confidence boost parameters for graph retrieval."""
    from .parameters import get_float_parameter
    return {
        "default_confidence_score": get_float_parameter(
            "graph_retrieval.default_confidence_score", 0.80
        ),
        "temporal_decay_lambda": get_float_parameter(
            "graph_retrieval.temporal_decay_lambda", 0.34657
        ),
        "max_temporal_boost": get_float_parameter(
            "graph_retrieval.max_temporal_boost", 0.10
        ),
        "max_confidence_boost": get_float_parameter(
            "graph_retrieval.max_confidence_boost", 0.08
        ),
    }


def get_graph_signal_parameters() -> Dict[str, float]:
    """Return SSOT GS-10 composite graph signal scoring thresholds and penalties."""
    from .parameters import get_float_parameter
    return {
        "penalty_mutex_conflict": get_float_parameter(
            "graph_signal.penalty_mutex_conflict", 5.0
        ),
        "penalty_orphan_edge": get_float_parameter(
            "graph_signal.penalty_orphan_edge", 50.0
        ),
        "min_density_rho": get_float_parameter(
            "graph_signal.min_density_rho", 2.0
        ),
        "min_typedness_tau": get_float_parameter(
            "graph_signal.min_typedness_tau", 0.5
        ),
        "min_confidence_mean": get_float_parameter(
            "graph_signal.min_confidence_mean", 0.7
        ),
        "min_grounding_depth": get_float_parameter(
            "graph_signal.min_grounding_depth", 0.98
        ),
        "min_temporal_freshness": get_float_parameter(
            "graph_signal.min_temporal_freshness", 0.6
        ),
        "min_approved_lane_purity": get_float_parameter(
            "graph_signal.min_approved_lane_purity", 1.0
        ),
        "max_orphan_ratio": get_float_parameter(
            "graph_signal.max_orphan_ratio", 0.0
        ),
        "min_composite_score": get_float_parameter(
            "graph_signal.min_composite_score", 80.0
        ),
    }


def get_index_core_parameters() -> Dict[str, int]:
    """Return SSOT hybrid index batching, facet character, anchor injection, and tile limits."""
    from .parameters import get_int_parameter
    return {
        "max_colbert_tokens": get_int_parameter("index_core.max_colbert_tokens", 32),
        "embedding_batch_size": get_int_parameter("index_core.embedding_batch_size", 16),
        "entity_facet_max_assertions": get_int_parameter(
            "index_core.entity_facet_max_assertions", 6
        ),
        "entity_facet_max_assertion_text_characters": get_int_parameter(
            "index_core.entity_facet_max_assertion_text_characters", 220
        ),
        "entity_facet_max_text_characters": get_int_parameter(
            "index_core.entity_facet_max_text_characters", 1600
        ),
        "max_exact_anchor_injections_per_lane": get_int_parameter(
            "index_core.max_exact_anchor_injections_per_lane", 8
        ),
        "max_exact_entity_title_matches_per_lane": get_int_parameter(
            "index_core.max_exact_entity_title_matches_per_lane", 8
        ),
        "default_colbert_tile_size": get_int_parameter(
            "index_core.default_colbert_tile_size", 64
        ),
    }


def get_document_security_parameters() -> Dict[str, Any]:
    """Return SSOT XML, ZIP, and JSON parser security limits."""
    from .parameters import get_float_parameter, get_int_parameter
    return {
        "max_xml_depth": get_int_parameter("document_security.max_xml_depth", 32),
        "max_xml_elements": get_int_parameter("document_security.max_xml_elements", 10000),
        "max_zip_ratio": get_float_parameter("document_security.max_zip_ratio", 20.0),
        "max_json_depth": get_int_parameter("document_security.max_json_depth", 32),
    }


def get_decision_service_parameters() -> Dict[str, Any]:
    """Return SSOT fast-path TTL, context record cap, and HTTP request byte ceiling."""
    from .parameters import get_float_parameter, get_int_parameter
    return {
        "default_fast_path_ttl_sec": get_float_parameter(
            "decision_service.default_fast_path_ttl_sec", 30.0
        ),
        "max_context_records": get_int_parameter(
            "decision_service.max_context_records", 12
        ),
        "max_request_bytes": get_int_parameter(
            "decision_service.max_request_bytes", 1048576
        ),
    }


def get_authorization_parameters() -> Dict[str, int]:
    """Return SSOT RS256 JWT lifetime and clock skew parameters."""
    from .parameters import get_int_parameter
    return {
        "max_token_lifetime_seconds": get_int_parameter(
            "authorization.max_token_lifetime_seconds", 3600
        ),
        "clock_skew_seconds": get_int_parameter(
            "authorization.clock_skew_seconds", 60
        ),
    }


def get_max_subagent_context_tokens() -> int:
    """Return SSOT token ceiling for slim subagent context profiles."""
    from .parameters import get_int_parameter
    return get_int_parameter("agent_architecture.max_subagent_context_tokens", 4000)


def get_starvation_turn_ratio_threshold() -> float:
    """Return SSOT speaker subcluster starvation turn ratio threshold."""
    from .parameters import get_float_parameter
    return get_float_parameter("audio_ingestion.starvation_turn_ratio_threshold", 0.05)


def get_default_assumption_ttl_days() -> int:
    """Return SSOT default TTL in days for exploratory assumptions."""
    from .parameters import get_int_parameter
    return get_int_parameter("l0.default_assumption_ttl_days", 14)


def get_l0_quarantine_thresholds() -> Dict[str, float]:
    """Return SSOT L0 quarantine routing thresholds (confidence, strategic value, evidence, tacticality)."""
    from .parameters import get_float_parameter
    return {
        "confidence_min": get_float_parameter("l0.quarantine_thresholds.confidence_min", 0.60),
        "strategic_value_min": get_float_parameter("l0.quarantine_thresholds.strategic_value_min", 0.40),
        "evidence_quality_min": get_float_parameter("l0.quarantine_thresholds.evidence_quality_min", 0.50),
        "tacticality_max": get_float_parameter("l0.quarantine_thresholds.tacticality_max", 0.85),
    }


def get_bge_m3_retrieval_weight_profiles() -> Dict[str, Dict[str, float]]:
    """Return route-adaptive BGE-M3 hybrid RRF weight profiles (simplex-constrained sum=1.0)."""
    return {
        "factual": {"dense": 0.05, "sparse": 0.15, "bm25": 0.70, "colbert": 0.10},
        "strategic": {"dense": 0.20, "sparse": 0.15, "bm25": 0.45, "colbert": 0.20},
        "guidance": {"dense": 0.20, "sparse": 0.15, "bm25": 0.40, "colbert": 0.25},
        "status_snapshot_current": {"dense": 0.20, "sparse": 0.15, "bm25": 0.40, "colbert": 0.25},
        "status_snapshot_history": {"dense": 0.20, "sparse": 0.15, "bm25": 0.40, "colbert": 0.25},
        "candidate_review": {"dense": 0.22, "sparse": 0.18, "bm25": 0.35, "colbert": 0.25},
        "candidate_evaluation": {"dense": 0.25, "sparse": 0.15, "bm25": 0.35, "colbert": 0.25},
        "candidate_guidance": {"dense": 0.22, "sparse": 0.18, "bm25": 0.35, "colbert": 0.25},
        "entity_navigation": {"dense": 0.25, "sparse": 0.25, "bm25": 0.20, "colbert": 0.30},
        "candidate_entity_navigation": {"dense": 0.25, "sparse": 0.25, "bm25": 0.20, "colbert": 0.30},
        "calibrated_hybrid": {"dense": 0.20, "sparse": 0.15, "bm25": 0.40, "colbert": 0.25},
    }


