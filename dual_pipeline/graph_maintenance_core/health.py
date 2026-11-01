"""
dual_pipeline/graph_maintenance_core/health.py

Graph health auditing engine.
Computes bloat ratio, ghost duplicate assertions, orphaned evidence records,
and recommends graph_gc when bloat ratio exceeds 1.5x threshold.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .dedup import find_semantic_duplicates
from .posture_ledger import snapshot_graph_vs_archive_posture

logger = logging.getLogger(__name__)


def _count_graph_entities(cursor: Any, tables: set) -> Tuple[int, int, int, int, int]:
    total_nodes = cursor.execute("SELECT count(*) FROM node").fetchone()[0] if "node" in tables else 0
    total_assertions, current_assertions = 0, 0
    if "assertion" in tables:
        total_assertions = cursor.execute("SELECT count(*) FROM assertion").fetchone()[0]
        cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
        if "currentness_state" in cols:
            current_assertions = cursor.execute("SELECT count(*) FROM assertion WHERE currentness_state = 'current'").fetchone()[0]
        else:
            current_assertions = total_assertions
    total_docs, active_docs = 0, 0
    if "search_document" in tables:
        total_docs = cursor.execute("SELECT count(*) FROM search_document").fetchone()[0]
        s_cols = {r[1] for r in cursor.execute("PRAGMA table_info(search_document)").fetchall()}
        if "retrieval_eligible" in s_cols:
            active_docs = cursor.execute("SELECT count(*) FROM search_document WHERE retrieval_eligible = 1").fetchone()[0]
        else:
            active_docs = total_docs
    return total_nodes, total_assertions, current_assertions, total_docs, active_docs


def calculate_graph_health_metrics(conn: Any) -> Dict[str, Any]:
    """Calculates comprehensive graph health, bloat, and hygiene metrics."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cursor.fetchall()}

    (
        total_nodes,
        total_assertions,
        current_assertions,
        total_docs,
        active_docs,
    ) = _count_graph_entities(cursor, tables)

    # Check ghost / duplicate assertions
    duplicates = find_semantic_duplicates(conn, similarity_threshold=0.9)
    ghost_assertions_count = len(duplicates)

    # Check orphaned evidence
    orphaned_evidence_count = 0
    if "assertion_evidence" in tables and "assertion" in tables:
        cursor.execute(
            """SELECT count(*) FROM assertion_evidence
                WHERE assertion_id NOT IN (SELECT assertion_id FROM assertion)"""
        )
        orphaned_evidence_count = cursor.fetchone()[0]

    # Calculate bloat ratios
    assertion_bloat_ratio = round(total_assertions / max(1, current_assertions), 2)
    doc_bloat_ratio = round(total_docs / max(1, active_docs), 2)
    max_bloat = max(assertion_bloat_ratio, doc_bloat_ratio)

    needs_gc = (max_bloat > 1.5) or (ghost_assertions_count > 0) or (orphaned_evidence_count > 0)
    recommendation = "RUN_GRAPH_GC" if needs_gc else "HEALTHY"

    return {
        "status": "PASS",
        "recommendation": recommendation,
        "needs_gc": needs_gc,
        "max_bloat_ratio": max_bloat,
        "assertion_bloat_ratio": assertion_bloat_ratio,
        "document_bloat_ratio": doc_bloat_ratio,
        "counts": {
            "total_nodes": total_nodes,
            "total_assertions": total_assertions,
            "current_assertions": current_assertions,
            "historical_assertions": total_assertions - current_assertions,
            "total_search_documents": total_docs,
            "active_search_documents": active_docs,
            "ghost_duplicate_assertions": ghost_assertions_count,
            "orphaned_evidence_records": orphaned_evidence_count,
        },
        "graph_vs_archive_posture": snapshot_graph_vs_archive_posture(conn),
        "duplicate_proposals_sample": duplicates[:10],
        "duplicate_proposals": duplicates,
    }


def render_health_report_markdown(metrics: Dict[str, Any], target_graph: str = "GraphDB") -> str:
    """Renders graph health metrics as a readable markdown document."""
    counts = metrics["counts"]
    rec = metrics["recommendation"]
    status_icon = "🟢" if rec == "HEALTHY" else "⚠️"

    lines = [
        "# Knowledge Graph Health & Hygiene Audit",
        "",
        f"> **Target Graph**: `{target_graph}`",
        f"> **Overall Health**: {status_icon} **`{rec}`** (Max Bloat Ratio: `{metrics['max_bloat_ratio']}x`)",
        "",
        "## 1. Graph Entity & Assertion Inventory",
        "",
        "| Metric | Count | Ratio / State |",
        "| :--- | :--- | :--- |",
        f"| **Total Nodes** | `{counts['total_nodes']}` | Active entity spine |",
        f"| **Active Assertions** | `{counts['current_assertions']}` | Current retrievable state |",
        f"| **Historical Assertions** | `{counts['historical_assertions']}` | Superseded / archived |",
        f"| **Assertion Bloat Ratio** | `{metrics['assertion_bloat_ratio']}x` | {'Threshold exceeded (>1.5x)' if metrics['assertion_bloat_ratio'] > 1.5 else 'Normal (<1.5x)'} |",
        f"| **Active Search Documents** | `{counts['active_search_documents']}` | Retrievable FTS/sidecar entries |",
        f"| **Total Search Documents** | `{counts['total_search_documents']}` | All indexed entries |",
        f"| **Document Bloat Ratio** | `{metrics['document_bloat_ratio']}x` | {'Threshold exceeded (>1.5x)' if metrics['document_bloat_ratio'] > 1.5 else 'Normal (<1.5x)'} |",
        "",
        "## 2. Integrity & Anomaly Checks",
        "",
        f"- **Ghost Duplicate Assertions**: `{counts['ghost_duplicate_assertions']}`",
        f"- **Orphaned Evidence Records**: `{counts['orphaned_evidence_records']}`",
        f"- **Recommended Action**: `{'Run python tools/graph_gc.py to archive stale nodes and deduplicate assertions' if metrics['needs_gc'] else 'No maintenance required'}`",
        "",
    ]
    return "\n".join(lines)
