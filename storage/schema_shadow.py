"""
Dual-Artery Enterprise Hybrid Architecture: Encrypted Shadow Schema Definitions.
Enforces Law 1 and Law 2 via encrypted schema DDL and target_entity_hmac indexing.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).parent / "schemas"


@functools.lru_cache(maxsize=1)
def get_shadow_schema_ddl() -> str:
    """Returns the DDL for the encrypted shadow graph tables."""
    return (SCHEMAS_DIR / "shadow_advisory.sql").read_text(encoding="utf-8")


def init_shadow_schema(conn: Any) -> None:
    """Initializes encrypted shadow graph schema and migrates target_entity_hmac column if missing."""
    # Migration check: ensure target_entity_hmac column exists if table was created in older version
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(shadow_graph_nodes)").fetchall()]
        if cols and "target_entity_hmac" not in cols:
            conn.execute("ALTER TABLE shadow_graph_nodes ADD COLUMN target_entity_hmac TEXT")
    except Exception:
        pass

    conn.executescript(get_shadow_schema_ddl())
