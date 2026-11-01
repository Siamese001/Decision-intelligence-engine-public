"""
tests/end_to_end/test_bge_m3_isotropic_routing_e2e.py

End-to-End lifecycle test verifying isotropic BGE-M3 intent routing integration.
"""

from __future__ import annotations

import time

from dual_pipeline.query_router_core.isotropic_router import (
    CANONICAL_INTENTS,
    IsotropicBgeM3Router,
)


def test_e2e_all_canonical_intents_resolvable_in_submillisecond():
    """Verify that every canonical intent centroid can be resolved with high margin in <1ms."""
    router = IsotropicBgeM3Router(dimension=1024, margin_threshold=0.15)
    # Warmup
    _ = router.route_vector(router.centroids[CANONICAL_INTENTS[0]])

    for intent in CANONICAL_INTENTS:
        centroid_vec = router.centroids[intent]
        start = time.perf_counter()
        result = router.route_vector(centroid_vec)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert result.top_intent == intent
        assert result.hitl_required is False
        assert result.margin_delta >= 0.15
        assert elapsed_ms < 1.0, f"Routing latency {elapsed_ms:.3f}ms exceeded 1ms target!"
