"""Retrieval release core package."""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..authorization import AuthorizationContext
from ..contracts import create_artifact, validate_artifact
from ..core import (
    ROOT,
    assert_safe_mutable_path,
    canonical_json,
    connect_sqlite,
    digest,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from ..graph_retrieval import governed_bm25_assertion_ranks
from ..index import CLASSIFICATION_RANK, EmbeddingBackend, HybridRetriever
from ..release_core.helpers import _verify_receipt
from ..release_core.manager import ReleaseManager
from .constants import (
    _BGE_BACKENDS,
    _MIN_DELTA_CASE_COUNTS,
    _PERFECT_METRIC_THRESHOLDS,
    _POLICY_THRESHOLD_FIELDS,
    _POSITIVE_QUERY_KINDS,
    _QUERY_KINDS,
    PAIR_FIELDS,
    RETRIEVAL_RELEASE_POLICY_PATH,
)
from .evaluator import (
    RetrievalReleaseEvaluator,
    _evaluate_case,
    validate_activation_evaluation,
)
from .metrics import (
    _critical_case_failed,
    _first_rank,
    _max_rank_regression,
    _metrics,
    _ndcg_at_k,
    _reciprocal_rank,
    _records_by_id,
    _repeat_rank_drift,
    _scoped_release_delta,
)
from .policies import (
    _assert_bge_m3_model_binding,
    _assert_real_bge_backend,
    _assert_suite_authorized,
    _load_policy,
    _pair_binding,
    _policy_binding,
    _same_pair,
)
from .preflight import (
    RetrievalSchemaPreflightError,
    preflight_retrieval_release_schema,
)

__all__ = [
    "RetrievalSchemaPreflightError",
    "preflight_retrieval_release_schema",
    "Any",
    "AuthorizationContext",
    "CLASSIFICATION_RANK",
    "Counter",
    "EmbeddingBackend",
    "HybridRetriever",
    "PAIR_FIELDS",
    "Path",
    "RETRIEVAL_RELEASE_POLICY_PATH",
    "ROOT",
    "ReleaseManager",
    "RetrievalReleaseEvaluator",
    "_BGE_BACKENDS",
    "_MIN_DELTA_CASE_COUNTS",
    "_PERFECT_METRIC_THRESHOLDS",
    "_POLICY_THRESHOLD_FIELDS",
    "_POSITIVE_QUERY_KINDS",
    "_QUERY_KINDS",
    "_assert_bge_m3_model_binding",
    "_assert_real_bge_backend",
    "_assert_suite_authorized",
    "_critical_case_failed",
    "_evaluate_case",
    "_first_rank",
    "_load_policy",
    "_max_rank_regression",
] + [
    "_metrics",
    "_ndcg_at_k",
    "_pair_binding",
    "_policy_binding",
    "_reciprocal_rank",
    "_records_by_id",
    "_repeat_rank_drift",
    "_same_pair",
    "_scoped_release_delta",
    "_verify_receipt",
    "annotations",
    "assert_safe_mutable_path",
    "canonical_json",
    "connect_sqlite",
    "create_artifact",
    "digest",
    "governed_bm25_assertion_ranks",
    "json",
    "math",
    "re",
    "read_json",
    "sha256_file",
    "stable_id",
    "time",
    "utc_now",
    "validate_activation_evaluation",
    "validate_artifact",
    "write_json_exclusive",
]
