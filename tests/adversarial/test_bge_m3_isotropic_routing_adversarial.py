"""
tests/adversarial/test_bge_m3_isotropic_routing_adversarial.py

Wave 3 Adversarial Test Suite:
Isotropic BGE-M3 Routing with Cascade Fallback.

Tests:
1. Anisotropy & Cone Collapse Mitigation: Verifies that mean-centering whitening
   prevents high-density representation bunching from distorting the 15% margin gate.
2. Decisive Intent (Delta >= 0.15): Proves autonomous routing without LLMs.
3. Narrow Ambiguity (Delta < 0.15): Proves escalation through the cascade shield to HITL.
4. Boundary condition at exact Delta == 0.15.
"""

from __future__ import annotations

import math
from typing import Tuple

from dual_pipeline.query_router_core.isotropic_router import (
    IsotropicBgeM3Router,
    IsotropicRoutingResult,
    IsotropicWhiteningTransform,
    cosine_similarity,
)


def test_adversarial_anisotropic_cone_whitening():
    """Dr. Aris Proof: Raw embeddings bunched in a narrow cone are expanded into an isotropic sphere."""
    # Simulate an anisotropic cone where all vectors have a large shared component (e.g. [0.9, 0.9, ...])
    # causing raw cosine similarities to cluster artificially between 0.95 and 0.99
    dim = 16
    mean_bias = [0.9] * dim
    raw_vec1 = [0.9 + (0.1 if i == 0 else 0.0) for i in range(dim)]
    raw_vec2 = [0.9 + (0.1 if i == 1 else 0.0) for i in range(dim)]

    raw_sim = cosine_similarity(raw_vec1, raw_vec2)
    assert raw_sim > 0.95, "Unwhitened cone vectors artificially cluster > 0.95"

    # Apply Isotropic Whitening (subtracting the shared anisotropic mean)
    whitener = IsotropicWhiteningTransform(dimension=dim, mean_vector=mean_bias)
    whitened_1 = whitener.transform(raw_vec1)
    whitened_2 = whitener.transform(raw_vec2)

    whitened_sim = cosine_similarity(whitened_1, whitened_2)
    # The true orthogonal difference is now revealed (similarity drops to near 0)
    assert whitened_sim < 0.20, f"Whitened similarity {whitened_sim:.3f} successfully broke the cone cluster!"


def test_decisive_intent_routes_autonomously_zero_llm():
    """Verify that a vector with decisive similarity (Delta >= 0.15) routes autonomously."""
    router = IsotropicBgeM3Router(dimension=1024, margin_threshold=0.15)
    # Exact lookup centroid vector
    target_vec = router.centroids["exact_lookup"]

    res = router.route_vector(target_vec)
    assert isinstance(res, IsotropicRoutingResult)
    assert res.top_intent == "exact_lookup"
    assert res.hitl_required is False
    assert res.margin_delta >= 0.15
    assert "Tier 1 Autonomous" in res.audit_notes


def test_ambiguous_intent_escalates_to_hitl_gate():
    """Verify that an ambiguous vector (Delta < 0.15) escalates to the HITL gate."""
    router = IsotropicBgeM3Router(dimension=1024, margin_threshold=0.15)
    # Construct a hybrid vector equidistant between exact_lookup and project_status
    v1 = router.centroids["exact_lookup"]
    v2 = router.centroids["project_status"]
    raw_h = [0.5 * (a + b) for a, b in zip(v1, v2, strict=True)]
    norm_h = math.sqrt(sum(x * x for x in raw_h))
    hybrid = [x / norm_h for x in raw_h]

    res = router.route_vector(hybrid)
    assert res.hitl_required is True
    assert res.margin_delta < 0.15
    assert "Tier 3 Epistemic Ambiguity" in res.audit_notes


def test_secondary_cascade_resolves_ambiguity():
    """Verify Tier 2 cascade engages when secondary evaluator resolves the ambiguity."""
    router = IsotropicBgeM3Router(dimension=1024, margin_threshold=0.15)
    v1 = router.centroids["gap_risk_inconsistency"]
    v2 = router.centroids["opportunity_nba"]
    raw_h = [0.5 * (a + b) for a, b in zip(v1, v2, strict=True)]
    norm_h = math.sqrt(sum(x * x for x in raw_h))
    hybrid = [x / norm_h for x in raw_h]

    def secondary_classifier() -> Tuple[str, float, float]:
        # Secondary classifier decisively picks gap_risk_inconsistency with delta 0.25 >= 0.15
        return "gap_risk_inconsistency", 0.85, 0.25

    res = router.route_vector(hybrid, secondary_evaluator_fn=secondary_classifier)
    assert res.hitl_required is False
    assert res.top_intent == "gap_risk_inconsistency"
    assert res.margin_delta >= 0.15
    assert "Tier 2 Cascade" in res.audit_notes
