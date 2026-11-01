"""Hybrid retrieval weight calibration and quad-engine normalization."""
from __future__ import annotations

import json
import math
from typing import Optional


def calibrate_hybrid_weights(
    dense_weight: float = 0.05,
    sparse_weight: float = 0.15,
    colbert_weight: float = 0.10,
    bm25_weight: Optional[float] = None,
) -> str:
    """Validate and normalize dense, sparse, BM25, and ColBERT weights to sum to 1.0 for hybrid scoring.

    Supports Quad-Engine hybrid retrieval:
        dense (BGE-M3 Dense) + sparse (BGE-M3 Learned Lexical) + bm25 (SQLite FTS5) + colbert (MaxSim Late Interaction)

    Args:
        dense_weight: Dense embedding weight (alpha, default 0.05).
        sparse_weight: Lexical sparse weight (beta, default 0.15).
        colbert_weight: Multi-vector ColBERT weight (delta, default 0.10).
        bm25_weight: SQLite FTS5 lexical weight (gamma, default 0.70 for quad-engine, or 0.0 if explicitly 3-engine).
    """
    if bm25_weight is None:
        if (
            math.isclose(dense_weight, 0.05)
            and math.isclose(sparse_weight, 0.15)
            and math.isclose(colbert_weight, 0.10)
        ):
            bm25_w = 0.70
        else:
            bm25_w = 0.0
    else:
        bm25_w = float(bm25_weight)

    total = dense_weight + sparse_weight + bm25_w + colbert_weight
    if total <= 0:
        return json.dumps({"error": "Total weight must be positive."})

    norm_dense = round(dense_weight / total, 4)
    norm_sparse = round(sparse_weight / total, 4)
    norm_bm25 = round(bm25_w / total, 4)
    norm_colbert = round(1.0 - norm_dense - norm_sparse - norm_bm25, 4)

    weights_dict = {
        "dense_weight": norm_dense,
        "sparse_weight": norm_sparse,
        "colbert_weight": norm_colbert,
    }
    if bm25_w > 0 or bm25_weight is not None:
        weights_dict["bm25_weight"] = norm_bm25

    return json.dumps(
        {
            "status": "CALIBRATED",
            "normalized_weights": weights_dict,
            "sum": round(sum(weights_dict.values()), 4),
            "engine_count": 4 if "bm25_weight" in weights_dict else 3,
            "recommended_for": "BGE-M3 quad-engine hybrid retrieval with reciprocal rank fusion (BM25 + Dense + Sparse + ColBERT)",
        },
        indent=2,
    )
