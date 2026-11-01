"""Governed BM25-assisted, typed multi-hop graph retrieval.

Backward-compatibility facade delegating to `dual_pipeline.graph_retrieval_core`.
Preserves 100% symbol parity, retrieval clearance, and graph traversal guarantees.
"""
from __future__ import annotations

import copy
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .contracts import create_artifact
from .core import connect_sqlite, digest, sha256_file, stable_id
from .graph_retrieval_core import (
    _BM25_STOPWORDS,
    _EXPANSION_LIMITS,
    _MATERIALITY_BOOST,
    _QUERY_STOPWORDS,
    GRAPH_RETRIEVAL_POLICY,
    GRAPH_RETRIEVAL_POLICY_BODY,
    GenerationalGraphCache,
    _artifact_assertion_id,
    _artifact_entity_ids,
    _assertion_is_navigation_only,
    _authorized_records,
    _base_seed_score,
    _better_path,
    _edge_increment,
    _load_graph,
    _path,
    _question_tokens,
    bm25_query_tokens,
    cached_load_graph,
    expand_connected_retrieval,
    get_global_graph_cache,
    governed_bm25_assertion_ranks,
    read_graph_from_disk,
    traverse_k_hop_neighborhood,
)
from .graph_csr_sidecar import (
    CSRCompilationResult,
    CSRGraphReader,
    compile_csr_from_graph,
)
from .index import (
    CLASSIFICATION_RANK,
    normalize_assertion_scope,
    resolve_retrieval_mode,
    retrieval_metadata_from_artifact,
    validate_sidecar,
)
from .retrieval_clearance import retrieval_clearance

__all__ = [
    "Any",
    "CLASSIFICATION_RANK",
    "CSRCompilationResult",
    "CSRGraphReader",
    "compile_csr_from_graph",
    "GRAPH_RETRIEVAL_POLICY",
    "GRAPH_RETRIEVAL_POLICY_BODY",
    "GenerationalGraphCache",
    "Iterable",
    "Path",
    "_BM25_STOPWORDS",
    "_EXPANSION_LIMITS",
    "_MATERIALITY_BOOST",
    "_QUERY_STOPWORDS",
    "_artifact_assertion_id",
    "_artifact_entity_ids",
    "_assertion_is_navigation_only",
    "_authorized_records",
    "_base_seed_score",
    "_better_path",
    "_edge_increment",
    "_load_graph",
    "_path",
    "_question_tokens",
    "bm25_query_tokens",
    "cached_load_graph",
    "connect_sqlite",
    "copy",
    "create_artifact",
    "defaultdict",
    "digest",
    "expand_connected_retrieval",
    "get_global_graph_cache",
    "governed_bm25_assertion_ranks",
    "json",
    "math",
    "normalize_assertion_scope",
    "re",
    "read_graph_from_disk",
    "resolve_retrieval_mode",
    "retrieval_clearance",
    "retrieval_metadata_from_artifact",
    "sha256_file",
    "stable_id",
    "traverse_k_hop_neighborhood",
    "validate_sidecar",
]
