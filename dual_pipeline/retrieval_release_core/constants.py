"""Constants for retrieval release evaluation."""
from __future__ import annotations

from ..core import ROOT

RETRIEVAL_RELEASE_POLICY_PATH = (
    ROOT / "requirements" / "retrieval-release-evaluation-policy.json"
)
PAIR_FIELDS = (
    "generation_id",
    "graph_snapshot",
    "graph_sha256",
    "sidecar_sha256",
    "embedding_backend",
    "model_id",
    "model_revision",
    "model_manifest_digest",
    "production_qualified",
)
_BGE_BACKENDS = frozenset(
    {
        "flagembedding-bge-m3",
        "flagembedding-bge-m3-dev",
        "flagembedding-bge-m3-internal",
    }
)
_POLICY_THRESHOLD_FIELDS = (
    "min_core_recall_at_k",
    "min_core_mrr",
    "min_core_ndcg_at_k",
    "min_delta_recall_at_k",
    "min_delta_mrr",
    "min_delta_ndcg_at_k",
    "critical_top_k",
    "max_rank_regression",
    "repeat_run_count",
    "max_repeat_rank_drift",
    "max_latency_ms",
    "max_forbidden_leakage_count",
)
_QUERY_KINDS = frozenset(
    {
        "semantic_paraphrase",
        "alias",
        "relationship",
        "hard_negative",
        "lane_isolation",
        "acl_boundary",
        "stale_or_superseded",
    }
)
_POSITIVE_QUERY_KINDS = frozenset(
    {"semantic_paraphrase", "alias", "relationship"}
)
_PERFECT_METRIC_THRESHOLDS = (
    "min_core_recall_at_k",
    "min_core_mrr",
    "min_core_ndcg_at_k",
    "min_delta_recall_at_k",
    "min_delta_mrr",
    "min_delta_ndcg_at_k",
)
_MIN_DELTA_CASE_COUNTS = {
    "added_or_changed": {
        "semantic_paraphrase": 3,
        "alias": 1,
        "hard_negative": 1,
        "lane_isolation": 1,
        "acl_boundary": 1,
    },
    "removed": {"stale_or_superseded": 1},
}
