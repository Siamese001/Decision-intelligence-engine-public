"""
Dual-Artery Enterprise Hybrid Architecture: Sidecar Vector Schema Definitions.
DDL routines for dense vectors, learned sparse weights, ColBERT tokens, and retrieval records.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).parent / "schemas"


@functools.lru_cache(maxsize=1)
def get_sidecar_schema_ddl() -> str:
    """Returns the DDL for vector sidecar tables supporting Quad-Engine Hybrid Retrieval."""
    return (SCHEMAS_DIR / "sidecar_vector.sql").read_text(encoding="utf-8")


def init_sidecar_schema(conn: Any) -> None:
    """Initializes vector sidecar schema supporting Quad-Engine Hybrid Retrieval."""
    conn.executescript(get_sidecar_schema_ddl())
