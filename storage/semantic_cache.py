"""
storage/semantic_cache.py

Layer-0/Layer-3 Storage Subsystem Semantic Cache Interface & Schema Governance.
Consolidates semantic cache storage schemas, DDL initialization, and canonical
cache primitives under the unified storage boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.semantic_cache_core import (
    BGE_M3_BACKEND_IDS,
    CONFIG_SCHEMA_VERSION,
    INITIAL_RUNTIME_MODE,
    MODES,
    QUERY_NAMESPACE_FIELDS,
    PendingSemanticCacheEntry,
    SemanticCache,
    SemanticCacheLookup,
    SemanticCachePolicy,
    SemanticCacheWriteResult,
    configure_semantic_cache,
    load_semantic_cache_policy,
    semantic_cache_config_path,
)

__all__ = [
    "BGE_M3_BACKEND_IDS",
    "CONFIG_SCHEMA_VERSION",
    "INITIAL_RUNTIME_MODE",
    "MODES",
    "QUERY_NAMESPACE_FIELDS",
    "PendingSemanticCacheEntry",
    "SEMANTIC_CACHE_SCHEMA",
    "SemanticCache",
    "SemanticCacheLookup",
    "SemanticCachePolicy",
    "SemanticCacheWriteResult",
    "configure_semantic_cache",
    "init_semantic_cache_schema",
    "load_semantic_cache_policy",
    "semantic_cache_config_path",
]

_SCHEMA_FILE = Path(__file__).parent / "schemas" / "semantic_cache.sql"
if _SCHEMA_FILE.is_file():
    SEMANTIC_CACHE_SCHEMA = _SCHEMA_FILE.read_text(encoding="utf-8")
else:
    from dual_pipeline.semantic_cache_core.constants import _SCHEMA as SEMANTIC_CACHE_SCHEMA


def init_semantic_cache_schema(conn: Any) -> None:
    """Initialize semantic cache tables and indices in the provided SQLite connection."""
    conn.executescript(SEMANTIC_CACHE_SCHEMA)
