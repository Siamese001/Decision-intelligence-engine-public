"""Semantic cache core package."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import struct
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..core import (
    assert_safe_mutable_path,
    digest,
    is_link_or_reparse,
    read_json,
    stable_id,
    utc_now,
    write_json,
)
from .cache import SemanticCache
from .chain_cache import RunChainDigestCache, get_global_chain_cache
from .config import (
    _load_or_initialize_config,
    _policy_value,
    _runtime_config,
    configure_semantic_cache,
    load_semantic_cache_policy,
    semantic_cache_config_path,
)
from .constants import (
    _SCHEMA,
    BGE_M3_BACKEND_IDS,
    CONFIG_SCHEMA_VERSION,
    INITIAL_RUNTIME_MODE,
    MODES,
    QUERY_NAMESPACE_FIELDS,
)
from .helpers import (
    _pack_vector,
    _similarity,
    _unpack_vector,
)
from .merkle_cache import (
    MerkleSemanticCacheBoundary,
    build_merkle_cache_context,
    compute_subgraph_merkle_root,
    extract_earliest_stale_after,
    extract_evidence_nodes,
    is_temporally_stale,
    sanitize_output_for_cache_replay,
)
from .models import (
    PendingSemanticCacheEntry,
    SemanticCacheLookup,
    SemanticCachePolicy,
    SemanticCacheWriteResult,
)

__all__ = [
    "Any",
    "BGE_M3_BACKEND_IDS",
    "CONFIG_SCHEMA_VERSION",
    "INITIAL_RUNTIME_MODE",
    "MODES",
    "Mapping",
    "Path",
    "PendingSemanticCacheEntry",
    "QUERY_NAMESPACE_FIELDS",
    "SemanticCache",
    "RunChainDigestCache",
    "get_global_chain_cache",
    "SemanticCacheLookup",
    "SemanticCachePolicy",
    "SemanticCacheWriteResult",
    "_SCHEMA",
    "_load_or_initialize_config",
    "_pack_vector",
    "_policy_value",
    "_runtime_config",
    "_similarity",
    "_unpack_vector",
    "annotations",
    "assert_safe_mutable_path",
    "configure_semantic_cache",
    "dataclass",
    "digest",
    "dt",
    "files",
    "is_link_or_reparse",
    "json",
    "load_semantic_cache_policy",
    "math",
    "np",
    "re",
    "read_json",
    "semantic_cache_config_path",
    "stable_id",
    "struct",
    "utc_now",
    "write_json",
    "compute_subgraph_merkle_root",
    "extract_evidence_nodes",
    "extract_earliest_stale_after",
    "is_temporally_stale",
    "build_merkle_cache_context",
    "sanitize_output_for_cache_replay",
    "MerkleSemanticCacheBoundary",
]
