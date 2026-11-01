"""
Dual-Artery Enterprise Hybrid Architecture: Canonical & Evidence Schema Definitions.
Initializes immutable evidence log, canonical graph tables, W2 Semantic Spine, and FTS5.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).parent / "schemas"


@functools.lru_cache(maxsize=1)
def get_evidence_schema_ddl() -> str:
    """Returns the DDL for the evidence log and work queue tables."""
    return (SCHEMAS_DIR / "canonical_evidence.sql").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def get_canonical_schema_ddl() -> str:
    """Returns the DDL for canonical graph tables, views, triggers, assertions, and FTS5."""
    return (SCHEMAS_DIR / "canonical_graph.sql").read_text(encoding="utf-8")


def init_evidence_schema(conn: Any) -> None:
    """Initializes evidence and work queue schema on a given connection."""
    conn.executescript(get_evidence_schema_ddl())


def init_canonical_schema(conn: Any) -> None:
    """Initializes canonical public graph schema, W2 Semantic Spine tables, and FTS5 index."""
    conn.executescript(get_canonical_schema_ddl())
