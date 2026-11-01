"""
tools/deduplicate_semantic_assertions.py

CLI utility for identifying and deduplicating semantic duplicate assertions in the knowledge graph.
Groups assertions by (subject, predicate, object) and calculates sequence matcher similarity on claim_text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import warnings
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_maintenance_core.dedup import (  # noqa: E402
    apply_supersessions,
    find_semantic_duplicates,
)
from storage.connection import get_db_connection  # noqa: E402

logger = logging.getLogger("deduplicate_semantic_assertions")


def _build_physical_proof(graph_path: Path, pre_sha: str, pre_size: int, applied_count: int) -> Dict[str, Any]:
    post_bytes = graph_path.read_bytes()
    post_sha = hashlib.sha256(post_bytes).hexdigest()
    post_size = len(post_bytes)
    ro_conn = get_db_connection(graph_path, readonly=True)
    try:
        ic_row = ro_conn.execute("PRAGMA integrity_check").fetchone()
        integrity = str(ic_row[0]) if ic_row else "unknown"
        hist_cnt = ro_conn.execute(
            "SELECT COUNT(*) FROM assertion WHERE currentness_state='historical'"
        ).fetchone()[0]
    finally:
        ro_conn.close()
    return {
        "file_changed": post_sha != pre_sha or applied_count == 0,
        "pre_sha256": pre_sha,
        "post_sha256": post_sha,
        "pre_size": pre_size,
        "post_size": post_size,
        "integrity_check": integrity,
        "historical_assertions_readback": hist_cnt,
        "applied_delta": applied_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deduplicate semantic duplicate assertions in the active knowledge graph."
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=ROOT / "graph" / "nee_graph.sqlite",
        help="Path to SQLite knowledge graph database",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.9,
        help="Similarity threshold for fuzzy claim matching (default: 0.9)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply proposed supersessions to graph database",
    )
    parser.add_argument(
        "--include-hitl-approved",
        action="store_true",
        default=False,
        help="Include HITL-flagged proposals when applying supersessions",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to output JSON report",
    )

    args = parser.parse_args(argv)
    graph_path = args.graph.expanduser().resolve()
    if not graph_path.is_file():
        print(f"Error: Graph file not found at {graph_path}", file=sys.stderr)
        return 1

    pre_bytes = graph_path.read_bytes()
    pre_sha = hashlib.sha256(pre_bytes).hexdigest()
    pre_size = len(pre_bytes)

    conn = get_db_connection(graph_path, readonly=not args.apply)
    proposals = find_semantic_duplicates(conn, similarity_threshold=args.threshold)

    print(f"Found {len(proposals)} proposed duplicate assertion supersessions in {graph_path.name}")
    for idx, p in enumerate(proposals[:10], start=1):
        print(f"  [{idx}] Sim: {p['similarity']} | Subject: {p['subject_node_id']} | Predicate: {p['predicate']}")
        print(f"      Winner: {p['winner_id']} -> '{p['winner_claim'][:70]}'")
        print(f"      Loser:  {p['loser_id']}  -> '{p['loser_claim'][:70]}'")

    if len(proposals) > 10:
        print(f"  ... and {len(proposals) - 10} more proposals")

    applied_count = 0
    proof: Dict[str, Any] = {}
    if args.apply and proposals:
        warnings.warn(
            "Direct application via deduplicate_semantic_assertions.py is deprecated. "
            "Use `nee graph-consolidate release` for governed, release-managed graph updates.",
            DeprecationWarning,
            stacklevel=2,
        )
        applied_count = apply_supersessions(
            conn, proposals, include_hitl_approved=args.include_hitl_approved
        )
        conn.close()
        proof = _build_physical_proof(graph_path, pre_sha, pre_size, applied_count)
        print(f"[OK] Successfully applied {applied_count} assertion supersessions.")
    else:
        if proposals and not args.apply:
            print("Dry run mode: No changes made. Pass --apply to execute supersessions.")
        conn.close()

    if args.output:
        report = {
            "graph": str(graph_path),
            "threshold": args.threshold,
            "proposals_count": len(proposals),
            "applied_count": applied_count,
            "physical_proof": proof,
            "proposals": proposals,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Saved report to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
