"""Vector geometry metrics and margin tie-rate computations for semantic units."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold


def unpack_float32(blob: bytes) -> np.ndarray | None:
    """Unpack raw float32 bytes into a 1D float64 numpy array."""
    if len(blob) % 4 != 0:
        return None
    try:
        return np.frombuffer(blob, dtype="<f4").astype(np.float64)
    except Exception:
        return None


def compute_geometry_metrics(vectors: Sequence[np.ndarray]) -> dict[str, Any]:
    """Compute geometric metrics over a cohort of embedding vectors."""
    n = len(vectors)
    if n == 0:
        return {
            "count": 0,
            "mean_pairwise_cos": 0.0,
            "median_nn_cos": 0.0,
            "near_duplicate_share": 0.0,
        }
    M = np.stack(vectors).astype(np.float64)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1.0
    M /= norms
    if n == 1:
        return {
            "count": 1,
            "mean_pairwise_cos": 1.0,
            "median_nn_cos": 1.0,
            "near_duplicate_share": 0.0,
        }
    S = M @ M.T
    off_diag = S[~np.eye(n, dtype=bool)]
    mean_pairwise = float(np.mean(off_diag)) if len(off_diag) > 0 else 0.0

    np.fill_diagonal(S, -1.0)
    nn = S.max(axis=1)
    median_nn = float(np.median(nn))
    near_dup_share = float(np.mean(nn > 0.95))

    return {
        "count": n,
        "mean_pairwise_cos": round(mean_pairwise, 4),
        "median_nn_cos": round(median_nn, 4),
        "near_duplicate_share": round(near_dup_share, 4),
    }


def compute_margin_tie_rates(
    unit_vectors_by_type: Mapping[str, Sequence[np.ndarray]],
    queries: Sequence[np.ndarray],
    margin_threshold: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute top-2 margin delta and tie rate per unit type."""
    eff_threshold = (
        margin_threshold
        if margin_threshold is not None
        else get_hitl_ambiguity_margin_threshold()
    )
    if not queries:
        return {
            utype: {"query_count": 0, "median_delta": 0.0, "tie_rate": 0.0}
            for utype in unit_vectors_by_type
        }
    Q = np.stack(queries).astype(np.float64)
    q_norms = np.linalg.norm(Q, axis=1, keepdims=True)
    q_norms[q_norms < 1e-12] = 1.0
    Q /= q_norms

    results: dict[str, dict[str, Any]] = {}
    for utype, vecs in unit_vectors_by_type.items():
        if len(vecs) < 2:
            results[utype] = {
                "query_count": len(queries),
                "median_delta": 0.0,
                "tie_rate": 1.0,
            }
            continue
        V = np.stack(vecs).astype(np.float64)
        v_norms = np.linalg.norm(V, axis=1, keepdims=True)
        v_norms[v_norms < 1e-12] = 1.0
        V /= v_norms

        sims = Q @ V.T  # (num_queries, num_vectors)
        top2 = np.partition(sims, -2, axis=1)[:, -2:]
        top2 = np.sort(top2, axis=1)
        deltas = top2[:, 1] - top2[:, 0]
        ties = int(np.sum(deltas < eff_threshold))
        results[utype] = {
            "query_count": len(queries),
            "median_delta": round(float(np.median(deltas)), 4),
            "tie_rate": round(ties / len(queries), 4),
        }
    return results
