"""
storage/schema.py

Unified Schema Definitions, Referential Constraints & Traversal Indexes for GraphDB.
Provides canonical DDL, referential integrity foreign keys,
composite traversal indexes, and JSON expression indexes.
"""
from __future__ import annotations

from typing import Any, Dict, List

from storage.schema_canonical import (
    SCHEMAS_DIR,
    get_canonical_schema_ddl,
    get_evidence_schema_ddl,
    init_canonical_schema,
    init_evidence_schema,
)

# Composite traversal and JSON expression indexes
TRAVERSAL_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_assertion_subj_pred 
    ON assertion(subject_node_id, predicate);

CREATE INDEX IF NOT EXISTS idx_assertion_qualifiers_card
    ON assertion(json_extract(qualifiers_json, '$.card_id'))
    WHERE qualifiers_json IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_assertion_allowed_audiences
    ON assertion(json_extract(allowed_audiences_json, '$[0]'))
    WHERE allowed_audiences_json IS NOT NULL;
"""


def ensure_traversal_indexes(conn: Any) -> List[str]:
    """Applies composite traversal and JSON expression indexes to assertion table."""
    conn.executescript(TRAVERSAL_INDEX_DDL)
    return [
        "idx_assertion_subj_pred",
        "idx_assertion_qualifiers_card",
        "idx_assertion_allowed_audiences",
    ]


def verify_referential_integrity(conn: Any) -> Dict[str, Any]:
    """Verifies SQLite foreign key and integrity pragmas.
    
    Returns:
        dict with status ('ok' or 'violation'), fk_violations list, and integrity_result.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    fk_violations = cur.execute("PRAGMA foreign_key_check;").fetchall()
    integrity_rows = cur.execute("PRAGMA integrity_check;").fetchall()
    integrity_ok = len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"

    return {
        "status": "ok" if (len(fk_violations) == 0 and integrity_ok) else "violation",
        "fk_violations": fk_violations,
        "foreign_key_violations": len(fk_violations),
        "integrity_check": "ok" if integrity_ok else str(integrity_rows),
    }


def init_graph_schema(conn: Any) -> None:
    """Initializes canonical schema and applies composite traversal indexes."""
    init_canonical_schema(conn)
    ensure_traversal_indexes(conn)
