"""Policy loading and validation for retrieval release evaluation."""

from __future__ import annotations

import math
import re
from typing import Any

from ..authorization import AuthorizationContext
from ..core import digest, read_json
from ..index import CLASSIFICATION_RANK, EmbeddingBackend
from ..parameters import get_float_parameter
from .constants import (
    _BGE_BACKENDS,
    _MIN_DELTA_CASE_COUNTS,
    _PERFECT_METRIC_THRESHOLDS,
    _POLICY_THRESHOLD_FIELDS,
    PAIR_FIELDS,
    RETRIEVAL_RELEASE_POLICY_PATH,
)


def _pair_binding(pair: dict[str, Any]) -> dict[str, Any]:
    value = {field: pair.get(field) for field in PAIR_FIELDS}
    if set(value) != set(PAIR_FIELDS) or any(value[field] is None for field in PAIR_FIELDS):
        raise ValueError("pair has incomplete retrieval-release binding")
    return value


def _assert_bge_m3_model_binding(pair: dict[str, Any], *, label: str) -> None:
    """Reject a receipt whose declared model cannot be the pinned BGE-M3 encoder."""

    if pair.get("embedding_backend") not in _BGE_BACKENDS:
        raise PermissionError(f"{label} requires an approved BGE-M3 embedding backend")
    if pair.get("model_id") != "BAAI/bge-m3":  # noqa: SSOT_EXEMPT
        raise PermissionError(f"{label} requires model_id BAAI/bge-m3")
    if not re.fullmatch(r"[0-9a-f]{40}", str(pair.get("model_revision", ""))):
        raise PermissionError(f"{label} requires an immutable BGE-M3 model revision")
    if not re.fullmatch(r"[0-9a-f]{64}", str(pair.get("model_manifest_digest", ""))):
        raise PermissionError(f"{label} requires a BGE-M3 model manifest digest")


def _same_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _pair_binding(left) == _pair_binding(right)


def _policy_binding(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_digest": digest(policy),
        "owner": policy["owner"],
    }


def _load_policy() -> dict[str, Any]:
    import sys

    mod = sys.modules.get("dual_pipeline.retrieval_release")
    reader = getattr(mod, "read_json", read_json) if mod else read_json
    policy = reader(RETRIEVAL_RELEASE_POLICY_PATH)
    if not isinstance(policy, dict):
        raise ValueError("retrieval release policy must be an object")
    required = {
        "schema_version",
        "policy_id",
        "policy_version",
        "owner",
        "thresholds",
        "delta_case_requirements",
    }
    if set(policy) != required or policy["schema_version"] != "1.0.0":
        raise ValueError("retrieval release policy shape is invalid")
    thresholds = policy["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != set(_POLICY_THRESHOLD_FIELDS):
        raise ValueError("retrieval release policy thresholds are invalid")
    numeric_fields = (
        "min_core_recall_at_k",
        "min_core_mrr",
        "min_core_ndcg_at_k",
        "min_delta_recall_at_k",
        "min_delta_mrr",
        "min_delta_ndcg_at_k",
        "max_latency_ms",
    )
    if (
        any(
            isinstance(thresholds[name], bool)
            or not isinstance(thresholds[name], (int, float))
            or not math.isfinite(thresholds[name])
            for name in numeric_fields
        )
        or isinstance(thresholds["critical_top_k"], bool)
        or not isinstance(thresholds["critical_top_k"], int)
        or isinstance(thresholds["max_rank_regression"], bool)
        or not isinstance(thresholds["max_rank_regression"], int)
        or isinstance(thresholds["repeat_run_count"], bool)
        or not isinstance(thresholds["repeat_run_count"], int)
        or isinstance(thresholds["max_repeat_rank_drift"], bool)
        or not isinstance(thresholds["max_repeat_rank_drift"], int)
        or isinstance(thresholds["max_forbidden_leakage_count"], bool)
        or not isinstance(thresholds["max_forbidden_leakage_count"], int)
        or not 0 <= thresholds["min_core_recall_at_k"] <= 1
        or not 0 <= thresholds["min_core_mrr"] <= 1
        or not 0 <= thresholds["min_core_ndcg_at_k"] <= 1
        or not 0 <= thresholds["min_delta_recall_at_k"] <= 1
        or not 0 <= thresholds["min_delta_mrr"] <= 1
        or not 0 <= thresholds["min_delta_ndcg_at_k"] <= 1
        or not 1 <= thresholds["critical_top_k"] <= 100
        or thresholds["max_rank_regression"] < 0
        or thresholds["repeat_run_count"] < 1
        or thresholds["max_repeat_rank_drift"] < 0
        or thresholds["max_latency_ms"] <= 0
        or thresholds["max_forbidden_leakage_count"] != 0
        or any(thresholds[name] != 1 for name in _PERFECT_METRIC_THRESHOLDS)
        or thresholds["critical_top_k"] != 1
        or thresholds["max_rank_regression"] != 0
        or thresholds["max_repeat_rank_drift"] != 0
        # This is a real, fully hybrid BGE-M3 retrieval budget.  It remains a
        # hard maximum, but allows the validated encoder to run all dense,
        # learned-sparse, BM25, and ColBERT components on the governed graph.
        or thresholds["max_latency_ms"]
        > get_float_parameter("timeouts.retrieval_steady_state_latency_ms", 7500.0)
    ):
        raise ValueError("retrieval release policy threshold values are unsafe")
    requirements = policy["delta_case_requirements"]
    if (
        not isinstance(requirements, dict)
        or set(requirements) != {"added_or_changed", "removed"}
        or any(
            not isinstance(requirements.get(change_kind), dict)
            for change_kind in ("added_or_changed", "removed")
        )
        or set(requirements["added_or_changed"])
        != {
            "semantic_paraphrase",
            "alias",
            "hard_negative",
            "lane_isolation",
            "acl_boundary",
        }
        or set(requirements["removed"]) != {"stale_or_superseded"}
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 1
            for change_requirements in requirements.values()
            for count in change_requirements.values()
        )
        or any(
            requirements[change_kind][query_kind] < minimum
            for change_kind, minimums in _MIN_DELTA_CASE_COUNTS.items()
            for query_kind, minimum in minimums.items()
        )
    ):
        raise ValueError("retrieval release delta case requirements are invalid")
    return policy


def _assert_real_bge_backend(pair: dict[str, Any], backend: EmbeddingBackend) -> None:
    _assert_bge_m3_model_binding(
        pair,
        label="retrieval release evaluation pair",
    )
    binding = {
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
        "production_qualified": backend.production_qualified,
    }
    if pair["embedding_backend"] != backend.backend_id or any(
        pair.get(name) != value for name, value in binding.items() if name != "embedding_backend"
    ):
        raise ValueError("evaluation backend does not match its graph/index pair")
    if (
        backend.backend_id not in _BGE_BACKENDS
        or backend.model_id != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
        or getattr(backend, "retrieval_encoder", None) != "real-bge-m3"
    ):
        raise PermissionError("only the real BGE-M3 encoder can qualify a retrieval release")
    verifier = getattr(backend, "verify_snapshot", None)
    if callable(verifier):
        verifier()


def _assert_suite_authorized(
    suite: dict[str, Any],
    authorization_context: AuthorizationContext,
) -> None:
    token_audiences = set(authorization_context.audiences)
    denied_cases = [
        case["case_id"]
        for case in suite["cases"]
        if (
            CLASSIFICATION_RANK[case["classification"]]
            > CLASSIFICATION_RANK[authorization_context.classification_clearance]
            or not set(case["audiences"]).issubset(token_audiences)
        )
    ]
    if denied_cases:
        raise PermissionError(
            "retrieval release suite exceeds authorization classification or "
            f"audience scope: {sorted(denied_cases)}"
        )
