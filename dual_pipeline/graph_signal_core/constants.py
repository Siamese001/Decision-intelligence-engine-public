"""Constants and thresholds for Graph Signal Integrity (GS-10 Standard)."""
from __future__ import annotations

from typing import Dict, Final, List

GRAPH_SIGNAL_SCHEMA_VERSION: Final[str] = "1.0.0"

# Predicates that must adhere to strict functional cardinality (<= 1 active edge per subject)
MUTEX_CARDINALITY_PREDICATES: Final[List[str]] = [
    "OWNS_OR_LEADS",
    "DELIVERS",
    "GOVERNS",
]

# Valid typed assertion classifications (excluding opaque text dumps)
TYPED_PREDICATES_EXCLUDED: Final[List[str]] = [
    "HAS_CLAIM_TEXT",
    "STATUS_REPORTED",
]

# Weights for Composite Graph Signal Score Q_net (sum = 1.0)
SIGNAL_WEIGHTS: Final[Dict[str, float]] = {
    "typedness": 0.20,
    "confidence": 0.15,
    "grounding": 0.20,
    "freshness": 0.15,
    "purity": 0.20,
    "trait_completeness": 0.10,
}

from dual_pipeline.parameters import get_graph_signal_parameters

_SIGNAL_PARAMS = get_graph_signal_parameters()

# Penalties applied to Q_net (SSOT config/runtime/parameters.json)
PENALTY_MUTEX_CONFLICT: Final[float] = float(_SIGNAL_PARAMS["penalty_mutex_conflict"])
PENALTY_ORPHAN_EDGE: Final[float] = float(_SIGNAL_PARAMS["penalty_orphan_edge"])

# Minimum passing thresholds for individual metrics
MIN_DENSITY_RHO: Final[float] = float(_SIGNAL_PARAMS["min_density_rho"])
MIN_TYPEDNESS_TAU: Final[float] = float(_SIGNAL_PARAMS["min_typedness_tau"])
MIN_CONFIDENCE_MEAN: Final[float] = float(_SIGNAL_PARAMS["min_confidence_mean"])
MIN_GROUNDING_DEPTH: Final[float] = float(_SIGNAL_PARAMS["min_grounding_depth"])
MIN_TEMPORAL_FRESHNESS: Final[float] = float(_SIGNAL_PARAMS["min_temporal_freshness"])
MIN_APPROVED_LANE_PURITY: Final[float] = float(_SIGNAL_PARAMS["min_approved_lane_purity"])
MAX_ORPHAN_RATIO: Final[float] = float(_SIGNAL_PARAMS["max_orphan_ratio"])
MIN_COMPOSITE_SCORE: Final[float] = float(_SIGNAL_PARAMS["min_composite_score"])
