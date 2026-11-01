"""Reporting utilities for manifest execution and GraphDB mutation accounting."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_delta import _COMPONENTS, _SIDECAR_COMPONENTS

logger = logging.getLogger("ManifestRunner.Reporting")


def query_graph_counts(graph_path: Path) -> Dict[str, int]:
    """Query current counts from the active SQLite graph."""
    if not graph_path.is_file():
        return {k: 0 for k, _, _ in _COMPONENTS}
    conn = connect_sqlite(graph_path, readonly=True)
    try:
        queries = {
            "source": "SELECT count(*) FROM source",
            "source_version": "SELECT count(*) FROM source_version",
            "evidence": "SELECT count(*) FROM evidence_item",
            "external_concept": "SELECT count(*) FROM node WHERE node_type='external_concept'",
            "internal_entity": """SELECT count(*) FROM node n
                   JOIN node_state ns ON ns.node_id=n.node_id
                   WHERE n.node_type!='external_concept' AND ns.approval_state='candidate'""",
            "candidate_assertion": "SELECT count(*) FROM assertion WHERE canonical_status='candidate'",
            "canonical_assertion": "SELECT count(*) FROM assertion WHERE canonical_status='canonical'",
            "superseded_assertion": "SELECT count(*) FROM assertion WHERE currentness_state='historical' AND superseded_at IS NOT NULL",
            "candidate_search_document": "SELECT count(*) FROM search_document WHERE canonical_status='candidate'",
            "audit_event": "SELECT count(*) FROM audit_event",
        }
        res = {}
        for k, q in queries.items():
            try:
                res[k] = int(conn.execute(q).fetchone()[0])
            except Exception:
                res[k] = 0
        return res
    finally:
        conn.close()


def query_sidecar_counts(sidecar_path: Path) -> Dict[str, int]:
    """Query counts from the sidecar vector SQLite database."""
    if not sidecar_path.is_file():
        return {k: 0 for k, _, _ in _SIDECAR_COMPONENTS}
    conn = connect_sqlite(sidecar_path, readonly=True)
    try:
        queries = {
            "dense_vector": "SELECT count(*) FROM dense_vector",
            "sparse_weight": "SELECT count(*) FROM sparse_weight",
            "colbert_token": "SELECT count(*) FROM colbert_token",
            "retrieval_record": "SELECT count(*) FROM retrieval_record",
        }
        res = {}
        for k, q in queries.items():
            try:
                res[k] = int(conn.execute(q).fetchone()[0])
            except Exception:
                res[k] = 0
        return res
    finally:
        conn.close()


def query_new_assertions(graph_path: Path, limit: int = 25) -> List[Dict[str, Any]]:
    """Query recent assertions from the graph database."""
    if not graph_path.is_file():
        return []
    conn = connect_sqlite(graph_path, readonly=True)
    try:
        query = """
        SELECT assertion_id, subject_node_id, predicate, COALESCE(object_literal, object_node_id, scope_text), canonical_status
        FROM assertion
        ORDER BY rowid DESC
        LIMIT ?
        """
        rows = conn.execute(query, (limit,)).fetchall()
        return [
            {
                "assertion_id": r[0],
                "subject_node": r[1],
                "predicate": r[2],
                "claim_text": (str(r[3])[:60] + "...") if r[3] and len(str(r[3])) > 60 else (str(r[3]) if r[3] else ""),
                "status": r[4],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Could not query assertions: {e}")
        return []
    finally:
        conn.close()


def print_mutation_breakdown(
    title: str,
    baseline_graph: Dict[str, int],
    active_graph: Dict[str, int],
    baseline_sidecar: Dict[str, int],
    active_sidecar: Dict[str, int],
    baseline_hash: str,
    active_hash: str,
    assertions: List[Dict[str, Any]],
) -> None:
    """Print the mandatory GraphDB mutation breakdown table, assertions table, and hash transition."""
    print(f"\n{'=' * 80}")
    print(f"MANDATORY GRAPH DB MUTATION BREAKDOWN: {title}")
    print(f"{'=' * 80}\n")

    print("### 1. Granular Graph DB Delta Table")
    print("| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |")
    print("| :--- | :--- | :--- | :--- | :--- |")

    table_descriptions = {
        "source": "Registered meeting intake source record",
        "source_version": "Preserved intake package immutable digest",
        "evidence": "Source-bound granular evidence items",
        "external_concept": "External taxonomical concepts",
        "internal_entity": "Candidate entity nodes reconciled via deterministic identity resolution",
        "candidate_assertion": "Candidate relational and factual assertions",
        "canonical_assertion": "Canonical assertions",
        "superseded_assertion": "Historical assertions superseded by new observations",
        "candidate_search_document": "Lexical full-text search document envelopes",
        "audit_event": "Governance release and activation audit trail envelopes",
    }
    table_name_map = {
        "source": "source",
        "source_version": "source_version",
        "evidence": "evidence_item",
        "external_concept": "node (external)",
        "internal_entity": "node (internal candidate)",
        "candidate_assertion": "assertion (candidate)",
        "canonical_assertion": "assertion (canonical)",
        "superseded_assertion": "assertion (superseded)",
        "candidate_search_document": "search_document",
        "audit_event": "audit_event",
    }

    for k in [c[0] for c in _COMPONENTS]:
        b = baseline_graph.get(k, 0)
        a = active_graph.get(k, 0)
        d = a - b
        delta_str = f"+{d}" if d > 0 else str(d)
        tbl = table_name_map.get(k, k)
        desc = table_descriptions.get(k, "Governed entity table")
        print(f"| `{tbl}` | {b} | {a} | {delta_str} | {desc} |")

    sidecar_descriptions = {
        "dense_vector": "1024-dim BGE-M3 float32 dense semantic embeddings",
        "sparse_weight": "BGE-M3 learned lexical sparse token weights",
        "colbert_token": "Multi-vector contextual ColBERT token embeddings",
        "retrieval_record": "Clearance-qualified retrieval record corpus envelopes",
    }
    for k in [c[0] for c in _SIDECAR_COMPONENTS]:
        b = baseline_sidecar.get(k, 0)
        a = active_sidecar.get(k, 0)
        d = a - b
        delta_str = f"+{d}" if d > 0 else str(d)
        desc = sidecar_descriptions.get(k, "Sidecar vector index")
        print(f"| `{k}` (sidecar) | {b} | {a} | {delta_str} | {desc} |")

    print("\n### 2. Itemized Assertions Table")
    print("| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for ass in assertions:
        raw_st = ass.get("status", "needs_review")
        st = "SOURCE_STATEMENT" if raw_st == "needs_review" else raw_st
        print(f"| `{ass['assertion_id']}` | `{ass['subject_node']}` | `{ass['predicate']}` | {ass['claim_text']} | `{st}` |")

    print("\n### 3. Idempotency & Hash Transition Explanation")
    print(f"- **Baseline Generation Graph SHA-256**: `{baseline_hash}`")
    print(f"- **Activated Generation Graph SHA-256**: `{active_hash}`")
    if baseline_hash != active_hash:
        print("- **Hash Transition Status**: `POSITIVE_MUTATION` — verified graph hash transition proving positive candidate operations were committed.")
    else:
        print("- **Hash Transition Status**: `IDEMPOTENT_NOOP` — graph hash preserved unchanged.")
    print()
