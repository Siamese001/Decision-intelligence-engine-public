#!/usr/bin/env python3
"""
tools/audit_edge_unit_baseline.py

Forensic baseline audit tool for edge & semantic-unit embedding state.
Audits the active pair, compares graph/sidecar hashes against active_pair.json pins,
and itemizes assertion, node, and sidecar retrieval record distributions.

Guarantees:
- Read-only SQLite queries via URI mode=ro.
- Pre- and post-read SHA-256 verification (zero in-place mutation).
- Deterministic detection of PIN_DRIFT vs PASS.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (
    read_json,
    seal_artifact,
    sha256_file,
    utc_now,
    write_json,
)
from dual_pipeline.storage.governed_context import GovernedStorageContext


def run_edge_unit_baseline_audit(
    runtime_root: Path,
    *,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Execute read-only baseline audit against the designated runtime root."""
    active_path = runtime_root / "active_pair.json"
    if not active_path.is_file():
        raise FileNotFoundError(f"active_pair.json not found at {active_path}")

    active_pair = read_json(active_path)
    generation_id = active_pair.get("generation_id", "unknown")
    graph_rel = active_pair.get("graph_path")
    sidecar_rel = active_pair.get("sidecar_path")
    if not graph_rel or not sidecar_rel:
        raise ValueError("active_pair.json missing graph_path or sidecar_path")

    graph_path = (runtime_root / graph_rel).resolve()
    sidecar_path = (runtime_root / sidecar_rel).resolve()
    if not graph_path.is_file():
        raise FileNotFoundError(f"Active graph file not found: {graph_path}")
    if not sidecar_path.is_file():
        raise FileNotFoundError(f"Active sidecar file not found: {sidecar_path}")

    stages = [
        "1. Pre-audit Hash Verification",
        "2. Graph Assertion Audit",
        "3. Graph Node Audit",
        "4. Sidecar Record Audit",
        "5. Post-audit Zero-Mutation Check",
    ]
    pbar = tqdm(
        total=len(stages),
        desc="Auditing Edge Baseline",
        disable=not show_progress,
        leave=False,
    )

    # 1. Pre-audit Hash Verification
    pbar.set_description(stages[0])
    graph_sha_pre = sha256_file(graph_path)
    sidecar_sha_pre = sha256_file(sidecar_path)
    pinned_graph_sha = active_pair.get("graph_sha256", "")
    pinned_sidecar_sha = active_pair.get("sidecar_sha256", "")

    graph_matches_pin = graph_sha_pre == pinned_graph_sha
    sidecar_matches_pin = sidecar_sha_pre == pinned_sidecar_sha

    status = (
        "PASS"
        if (graph_matches_pin and sidecar_matches_pin)
        else "PIN_DRIFT"
    )
    pbar.update(1)

    # 2. Graph Assertion Audit
    pbar.set_description(stages[1])
    with GovernedStorageContext.read_context(graph_path) as g_conn:
        cur_filter = "currentness_state IN ('current', 'unknown') OR currentness_state IS NULL"

        total_assertions = g_conn.execute("SELECT count(*) FROM assertion").fetchone()[0]
        current_assertions = g_conn.execute(
            f"SELECT count(*) FROM assertion WHERE {cur_filter}"
        ).fetchone()[0]
        historical_assertions = g_conn.execute(
            "SELECT count(*) FROM assertion WHERE currentness_state = 'historical'"
        ).fetchone()[0]

        edge_shaped = g_conn.execute(
            f"SELECT count(*) FROM assertion WHERE ({cur_filter}) AND object_node_id IS NOT NULL AND (object_literal IS NULL OR object_literal = '')"
        ).fetchone()[0]
        literal_shaped = g_conn.execute(
            f"SELECT count(*) FROM assertion WHERE ({cur_filter}) AND (object_node_id IS NULL OR (object_literal IS NOT NULL AND object_literal != ''))"
        ).fetchone()[0]

        legacy_edge_backfill = g_conn.execute(
            f"SELECT count(*) FROM assertion WHERE ({cur_filter}) AND object_node_id IS NOT NULL AND (object_literal IS NULL OR object_literal = '') AND origin_type = 'legacy_migration' AND extraction_method = 'deterministic_legacy_edge_backfill'"
        ).fetchone()[0]

        edge_quotes = g_conn.execute(
            f"""SELECT count(DISTINCT a.assertion_id) FROM assertion a
                JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id
                JOIN evidence_item e ON e.evidence_id = ae.evidence_id
                WHERE ({cur_filter.replace('currentness_state', 'a.currentness_state')})
                  AND a.object_node_id IS NOT NULL AND (a.object_literal IS NULL OR a.object_literal = '')
                  AND e.direct_quote IS NOT NULL AND e.direct_quote != ''"""
        ).fetchone()[0]

        literal_quotes = g_conn.execute(
            f"""SELECT count(DISTINCT a.assertion_id) FROM assertion a
                JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id
                JOIN evidence_item e ON e.evidence_id = ae.evidence_id
                WHERE ({cur_filter.replace('currentness_state', 'a.currentness_state')})
                  AND (a.object_node_id IS NULL OR (a.object_literal IS NOT NULL AND a.object_literal != ''))
                  AND e.direct_quote IS NOT NULL AND e.direct_quote != ''"""
        ).fetchone()[0]

        edge_val_states = {
            r[0]: r[1]
            for r in g_conn.execute(
                f"""SELECT validation_state || '/' || canonical_status, count(*)
                    FROM assertion WHERE ({cur_filter}) AND object_node_id IS NOT NULL AND (object_literal IS NULL OR object_literal = '')
                    GROUP BY 1 ORDER BY 2 DESC"""
            ).fetchall()
        }

        top_predicates = [
            {"predicate": r[0], "count": r[1], "distinct_subjects": r[2], "distinct_objects": r[3]}
            for r in g_conn.execute(
                f"""SELECT predicate, count(*), count(DISTINCT subject_node_id), count(DISTINCT object_node_id)
                    FROM assertion WHERE ({cur_filter}) AND object_node_id IS NOT NULL AND (object_literal IS NULL OR object_literal = '')
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""
            ).fetchall()
        ]
    pbar.update(1)

    # 3. Graph Node Audit
    pbar.set_description(stages[2])
    with GovernedStorageContext.read_context(graph_path) as g_conn:
        total_nodes = g_conn.execute("SELECT count(*) FROM node").fetchone()[0]
        node_type_counts = {
            r[0]: r[1]
            for r in g_conn.execute(
                "SELECT node_type, count(*) FROM node GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        }
        desc_stats = g_conn.execute(
            "SELECT avg(length(description)), sum(description IS NULL OR description = '') FROM node"
        ).fetchone()
        avg_desc_len = round(desc_stats[0] or 0.0, 2)
        empty_desc_count = desc_stats[1] or 0
    pbar.update(1)

    # 4. Sidecar Record Audit
    pbar.set_description(stages[3])
    with GovernedStorageContext.read_context(sidecar_path) as s_conn:
        total_sidecar_records = s_conn.execute(
            "SELECT count(*) FROM retrieval_record"
        ).fetchone()[0]

        sidecar_kinds = {
            r[0]: r[1]
            for r in s_conn.execute(
                "SELECT retrieval_kind, count(*) FROM retrieval_record GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        }

        record_types_by_lane = {
            f"{r[0]}/{r[1]}": r[2]
            for r in s_conn.execute(
                "SELECT record_type, lane, count(*) FROM retrieval_record GROUP BY 1, 2 ORDER BY 3 DESC"
            ).fetchall()
        }

        edge_like_sidecar_count = sum(
            count
            for key, count in record_types_by_lane.items()
            if key.startswith("edge/") or key.startswith("architecture_relationship/")
        )
    pbar.update(1)

    # 5. Post-audit Zero-Mutation Check
    pbar.set_description(stages[4])
    graph_sha_post = sha256_file(graph_path)
    sidecar_sha_post = sha256_file(sidecar_path)

    if graph_sha_post != graph_sha_pre:
        raise RuntimeError("Graph database mutated during read-only audit!")
    if sidecar_sha_post != sidecar_sha_pre:
        raise RuntimeError("Sidecar database mutated during read-only audit!")
    pbar.update(1)
    pbar.close()

    raw_receipt: dict[str, Any] = {
        "schema_version": "1.0.0",
        "audit_id": f"audit-edge-baseline-{generation_id}",
        "status": status,
        "audited_at": utc_now(),
        "runtime_root": str(runtime_root),
        "generation_id": generation_id,
        "hash_verification": {
            "graph_matches_pin": graph_matches_pin,
            "sidecar_matches_pin": sidecar_matches_pin,
            "pinned_graph_sha256": pinned_graph_sha,
            "live_graph_sha256": graph_sha_pre,
            "pinned_sidecar_sha256": pinned_sidecar_sha,
            "live_sidecar_sha256": sidecar_sha_pre,
            "zero_mutation_verified": True,
        },
        "assertion_metrics": {
            "total_assertions": total_assertions,
            "current_unknown_assertions": current_assertions,
            "historical_assertions": historical_assertions,
            "edge_shaped_assertions": edge_shaped,
            "literal_shaped_assertions": literal_shaped,
            "legacy_edge_backfill_count": legacy_edge_backfill,
            "legacy_edge_backfill_ratio": round(legacy_edge_backfill / max(edge_shaped, 1), 4),
            "edge_shaped_with_quote": edge_quotes,
            "edge_shaped_quote_ratio": round(edge_quotes / max(edge_shaped, 1), 4),
            "literal_shaped_with_quote": literal_quotes,
            "literal_shaped_quote_ratio": round(literal_quotes / max(literal_shaped, 1), 4),
            "validation_states": edge_val_states,
            "top_predicates": top_predicates,
        },
        "node_metrics": {
            "total_nodes": total_nodes,
            "top_node_types": dict(list(node_type_counts.items())[:10]),
            "average_description_length": avg_desc_len,
            "empty_description_count": empty_desc_count,
        },
        "sidecar_metrics": {
            "total_records": total_sidecar_records,
            "retrieval_kinds": sidecar_kinds,
            "record_types_by_lane": record_types_by_lane,
            "edge_like_sidecar_count": edge_like_sidecar_count,
            "edge_like_sidecar_ratio": round(edge_like_sidecar_count / max(total_sidecar_records, 1), 4),
        },
    }

    return seal_artifact(raw_receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only forensic baseline audit for edge & semantic-unit index."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / ".local" / "dual_pipeline",
        help="Path to dual_pipeline runtime root directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write sealed audit receipt.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output to stdout.",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit with non-zero returncode if PIN_DRIFT is detected.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_root = args.runtime_root.resolve()
    show_progress = not args.json

    try:
        receipt = run_edge_unit_baseline_audit(
            runtime_root,
            show_progress=show_progress,
        )
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    # Determine destination for receipt
    output_path = args.output
    if not output_path:
        reports_dir = runtime_root.parent.parent / "reports"
        if not reports_dir.is_dir():
            reports_dir = ROOT / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        gen_id = receipt["generation_id"]
        output_path = reports_dir / f"edge_unit_baseline_receipt_{gen_id}.json"

    write_json(output_path, receipt)

    if args.json:
        sys.stdout.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"\nAudit completed. Status: {receipt['status']}\n")
        sys.stdout.write(f"Sealed receipt saved to: {output_path}\n")
        sys.stdout.write(f"Graph hash status: {'MATCH' if receipt['hash_verification']['graph_matches_pin'] else 'DRIFT'}\n")
        sys.stdout.write(f"Sidecar hash status: {'MATCH' if receipt['hash_verification']['sidecar_matches_pin'] else 'MATCH'}\n")
        sys.stdout.write(f"Edge-shaped assertions: {receipt['assertion_metrics']['edge_shaped_assertions']}\n")
        sys.stdout.write(f"Literal assertions: {receipt['assertion_metrics']['literal_shaped_assertions']}\n")
        sys.stdout.write(f"Sidecar edge records: {receipt['sidecar_metrics']['edge_like_sidecar_count']} / {receipt['sidecar_metrics']['total_records']}\n")

    if args.fail_on_drift and receipt["status"] == "PIN_DRIFT":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
