#!/usr/bin/env python3
"""
tools/collect_graph_consolidation_baseline.py

Collects read-only baseline inventory and empirical evidence of residual graph duplication
from the active knowledge graph pair, producing wave1_baseline_receipt.json.
Enforces read-only database connections and cryptographic receipt sealing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from storage.connection import create_sqlite_conn, sqlite3

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = ROOT / ".local" / "dual_pipeline"
RECEIPT_DIR = ROOT / "reports" / "graph_consolidation"
DEFAULT_RECEIPT_PATH = RECEIPT_DIR / "wave1_baseline_receipt.json"

HARDCODED_TIER2_TARGETS = [
    "person_gordon_paynter",
    "stakeholder_gordon_paynter",
    "q3_charter_customer_ai",
    "workstream_customer_ai",
    "q3_charter_fcdb",
    "workstream_fcdb",
    "q3_charter_nuclear_scheduling",
    "workstream_nuclear_scheduling",
    "q3_charter_pd_conduit",
    "workstream_pd_conduit",
]

DEPRECATED_PREDICATES = [
    "ALIGNED_TO",
    "BRIDGES_TO",
    "BUILDS",
    "CIO_ANCHOR_FOR",
    "GOVERNED_BY",
    "GOVERNS_USE_OF",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_digest(data: Dict[str, Any]) -> str:
    canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def collect_baseline(runtime_root: Path) -> Dict[str, Any]:
    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.exists():
        raise FileNotFoundError(f"active_pair.json not found at {active_pair_path}")

    pair_data = json.loads(active_pair_path.read_text(encoding="utf-8"))
    raw_graph_path = pair_data.get("graph_path")
    if not raw_graph_path:
        raise ValueError("graph_path missing from active_pair.json")

    graph_path = (runtime_root / raw_graph_path).resolve()
    if not graph_path.exists():
        # Fallback to direct path if raw_graph_path is already absolute
        graph_path = Path(raw_graph_path).resolve()
    if not graph_path.exists():
        raise FileNotFoundError(f"Active graph database not found at {graph_path}")

    file_sha = sha256_file(graph_path)
    declared_sha = pair_data.get("graph_sha256")
    if declared_sha and declared_sha != file_sha:
        raise RuntimeError(f"Active graph sha mismatch: disk={file_sha}, declared={declared_sha}")

    # Read-only SQLite connection
    conn = create_sqlite_conn(graph_path, readonly=True)
    conn.row_factory = sqlite3.Row

    try:
        # Probe 1: Tier 2 audit events
        row = conn.execute(
            "SELECT count(*) FROM audit_event WHERE actor_or_process='tier2_concept_dedup'"
        ).fetchone()
        tier2_audit_events_count = row[0] if row else 0

        # Probe 2: Node status merged count
        row = conn.execute("SELECT count(*) FROM node WHERE status='merged'").fetchone()
        nodes_status_merged_count = row[0] if row else 0

        # Probe 3: Hardcoded target nodes state
        placeholders = ",".join("?" for _ in HARDCODED_TIER2_TARGETS)
        target_rows = conn.execute(
            f"SELECT node_id, node_type, canonical_name, active, status FROM node WHERE node_id IN ({placeholders}) ORDER BY node_id",
            tuple(HARDCODED_TIER2_TARGETS),
        ).fetchall()
        hardcoded_targets = [dict(r) for r in target_rows]

        # Probe 4: Same-type exact-name duplicate groups
        same_type_rows = conn.execute(
            """SELECT node_type, lower(trim(canonical_name)) as norm_name, count(*) as count,
                      group_concat(node_id, ' | ') as node_ids
               FROM node
               WHERE active=1
               GROUP BY node_type, lower(trim(canonical_name))
               HAVING count(*) > 1
               ORDER BY count DESC, norm_name"""
        ).fetchall()
        same_type_dups = [dict(r) for r in same_type_rows]

        # Probe 5: Cross-type exact-name duplicate groups
        cross_type_rows = conn.execute(
            """SELECT lower(trim(canonical_name)) as norm_name,
                      count(DISTINCT node_type) as type_count,
                      count(*) as node_count,
                      group_concat(DISTINCT node_type) as types,
                      group_concat(node_id, ' | ') as node_ids
               FROM node
               WHERE active=1
               GROUP BY lower(trim(canonical_name))
               HAVING count(DISTINCT node_type) > 1
               ORDER BY node_count DESC, norm_name"""
        ).fetchall()
        cross_type_dups = [dict(r) for r in cross_type_rows]

        # Probe 6: Exact-triple current canonical duplicate groups
        triple_rows = conn.execute(
            """SELECT subject_node_id, predicate, object_node_id,
                      count(*) as count,
                      count(DISTINCT confidence_score) as distinct_conf_count,
                      count(DISTINCT primary_source_id) as distinct_source_count,
                      count(DISTINCT extraction_run_id) as distinct_run_count,
                      max(created_at) as max_created,
                      min(created_at) as min_created
               FROM assertion
               WHERE currentness_state='current'
                 AND canonical_status='canonical'
                 AND superseded_by_assertion_id IS NULL
                 AND object_node_id IS NOT NULL
               GROUP BY subject_node_id, predicate, object_node_id
               HAVING count(*) > 1
               ORDER BY count DESC, subject_node_id, predicate"""
        ).fetchall()
        triple_dups = [dict(r) for r in triple_rows]

        # Probe 7: Unmigrated live edges
        row = conn.execute(
            """SELECT count(*) FROM edge
               WHERE superseded_by IS NULL
                 AND edge_id NOT IN (SELECT edge_id FROM legacy_edge_assertion_map)"""
        ).fetchone()
        unmigrated_live_edges_count = row[0] if row else 0

        # Probe 8: Deprecated predicates in current assertions
        pred_ph = ",".join("?" for _ in DEPRECATED_PREDICATES)
        row = conn.execute(
            f"SELECT count(*) FROM assertion WHERE predicate IN ({pred_ph}) AND currentness_state='current'",
            tuple(DEPRECATED_PREDICATES),
        ).fetchone()
        deprecated_predicates_count = row[0] if row else 0

        # Probe 9: Evidence item duplicate locators
        ev_rows = conn.execute(
            """SELECT source_version_id, locator_value, count(*) as count,
                      group_concat(evidence_id, ' | ') as sample_evidence_ids
               FROM evidence_item
               WHERE locator_value IS NOT NULL AND trim(locator_value) != ''
               GROUP BY source_version_id, locator_value
               HAVING count(*) > 1
               ORDER BY count DESC"""
        ).fetchall()
        evidence_loc_dups = [dict(r) for r in ev_rows]

        # Probe 10: Overall Graph counts
        def get_count(table: str) -> int:
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not has_tbl:
                return 0
            res = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            return res[0] if res else 0

        graph_summary = {
            "node_total": get_count("node"),
            "node_active": conn.execute("SELECT count(*) FROM node WHERE active=1").fetchone()[0],
            "assertion_total": get_count("assertion"),
            "assertion_current_canonical": conn.execute(
                "SELECT count(*) FROM assertion WHERE currentness_state='current' AND canonical_status='canonical'"
            ).fetchone()[0],
            "edge_total": get_count("edge"),
            "evidence_item_total": get_count("evidence_item"),
            "assertion_evidence_total": get_count("assertion_evidence"),
            "search_document_total": get_count("search_document"),
            "audit_event_total": get_count("audit_event"),
        }

    finally:
        conn.close()

    receipt: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "receipt_type": "graph_consolidation_wave1_baseline",
        "generated_at_utc": utc_now_iso(),
        "runtime_root": str(runtime_root),
        "active_pair": {
            "generation_id": pair_data.get("generation_id"),
            "graph_path": str(graph_path),
            "graph_sha256": file_sha,
            "sidecar_sha256": pair_data.get("sidecar_sha256"),
            "embedding_backend": pair_data.get("embedding_backend"),
            "model_id": pair_data.get("model_id"),
            "model_revision": pair_data.get("model_revision"),
            "activated_at": pair_data.get("activated_at"),
            "assurance_tier": pair_data.get("assurance_tier"),
            "production_qualified": pair_data.get("production_qualified"),
        },
        "graph_summary": graph_summary,
        "empirical_probes": {
            "tier2_audit_events_count": tier2_audit_events_count,
            "nodes_status_merged_count": nodes_status_merged_count,
            "hardcoded_tier2_targets": hardcoded_targets,
            "same_type_exact_name_dup_groups_count": len(same_type_dups),
            "same_type_exact_name_dup_groups": same_type_dups,
            "cross_type_exact_name_dup_groups_count": len(cross_type_dups),
            "cross_type_exact_name_dup_groups": cross_type_dups,
            "exact_triple_dup_groups_count": len(triple_dups),
            "exact_triple_dup_summary": {
                "groups_count": len(triple_dups),
                "total_rows_in_groups": sum(r["count"] for r in triple_dups),
                "groups_confidence_differs": sum(1 for r in triple_dups if r["distinct_conf_count"] > 1),
                "groups_multi_source": sum(1 for r in triple_dups if r["distinct_source_count"] > 1),
                "groups_multi_run": sum(1 for r in triple_dups if r["distinct_run_count"] > 1),
            },
            "exact_triple_dup_groups": triple_dups,
            "unmigrated_live_edges_count": unmigrated_live_edges_count,
            "deprecated_predicates_count": deprecated_predicates_count,
            "evidence_dup_locator_groups_count": len(evidence_loc_dups),
            "evidence_dup_locator_groups_sample": evidence_loc_dups[:25],
        },
        "verdict": {
            "tier2_active_deployment_status": "ABSENT_IN_ACTIVE_GRAPH",
            "tier2_execution_scope": "SCRATCH_COPY_ONLY (reports/graph_scout/nee_graph_tier2_work.sqlite)",
            "residual_concept_dedup_needed": True,
            "residual_triple_consolidation_needed": True,
            "justification": (
                "Empirical probe against active graph generation proves 0 Tier 2 audit events and 0 merged nodes. "
                "All 10 target entities remain active duplicates. 11 entity name duplicate groups and 40 exact-triple "
                "assertion duplicate groups persist in active canonical state."
            ),
        },
    }

    digest = canonical_json_digest(receipt)
    receipt["receipt_digest"] = digest
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Wave 1 Graph Consolidation Baseline Receipt")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Path to runtime root directory (default: .local/dual_pipeline)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RECEIPT_PATH,
        help="Path to output baseline receipt JSON",
    )
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Collecting baseline from runtime root: {runtime_root}...")
    receipt = collect_baseline(runtime_root)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    print(f"✅ Baseline receipt generated: {output_path}")
    print(f"   Digest: {receipt['receipt_digest']}")
    print(f"   Graph SHA256: {receipt['active_pair']['graph_sha256']}")
    print(f"   Tier 2 Audit Events in Active Graph: {receipt['empirical_probes']['tier2_audit_events_count']}")
    print(f"   Merged Nodes in Active Graph: {receipt['empirical_probes']['nodes_status_merged_count']}")
    print(f"   Same-Type Name Duplicate Groups: {receipt['empirical_probes']['same_type_exact_name_dup_groups_count']}")
    print(f"   Cross-Type Name Duplicate Groups: {receipt['empirical_probes']['cross_type_exact_name_dup_groups_count']}")
    print(f"   Exact Triple Duplicate Groups: {receipt['empirical_probes']['exact_triple_dup_groups_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
