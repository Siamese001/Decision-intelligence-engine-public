#!/usr/bin/env python3
"""Run the Governed Graph Health & Remediation Scout (Advisory Sub-Agent).

Usage:
    .venv/bin/python tools/run_graph_scout.py [--runtime-root <path>] [--graph <path>] [--output-dir <path>]

Enforces zero mutations on the active graph database. Produces contract-sealed
learning change proposals (learning-change-proposal:1.0.0) with approval_required=True
and auto_apply=False.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json
from dual_pipeline.graph_scout import GraphScout

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def _resolve_graph(runtime_root: Path, explicit_graph: Path | None) -> Path:
    if explicit_graph and explicit_graph.is_file():
        return explicit_graph

    pair_path = runtime_root / "active_pair.json"
    if pair_path.is_file():
        pair = read_json(pair_path)
        graph_rel = pair.get("graph_path")
        if graph_rel:
            resolved = (runtime_root / graph_rel).resolve()
            if resolved.is_file():
                return resolved

    # Fallback to default graph location
    default_graph = ROOT / "graph" / "nee_graph.sqlite"
    if default_graph.is_file():
        return default_graph

    raise FileNotFoundError(f"Cannot resolve active graph from runtime root: {runtime_root}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Governed Graph Health & Remediation Scout (Read-Only Advisory Sub-Agent)"
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / ".local" / "dual_pipeline",
        help="Runtime root directory containing active_pair.json",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=None,
        help="Path to SQLite graph database (defaults to active_pair.json binding)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write diagnostic report and proposals",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.72,
        help="Fuzzy similarity threshold for near-duplicate entity detection",
    )
    parser.add_argument(
        "--max-candidate-age-days",
        type=int,
        default=30,
        help="Age threshold in days for flagging unreviewed candidate assertions",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit only machine-readable JSON output",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Run in scheduled mode (evaluates mutation cadence and proposal queue backlog)",
    )
    parser.add_argument(
        "--cadence-mutations",
        type=int,
        default=10,
        help="Cadence threshold in mutations for scheduled scout",
    )
    parser.add_argument(
        "--cadence-days",
        type=int,
        default=7,
        help="Cadence threshold in days for scheduled scout",
    )
    parser.add_argument(
        "--max-proposal-backlog",
        type=int,
        default=20,
        help="Maximum open proposal backlog before scheduled scout is gated",
    )

    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()

    if args.scheduled:
        from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
        from dual_pipeline.proposal_queue import ProposalQueue

        # 1. Backpressure gate: proposal queue depth
        queue = ProposalQueue()
        if queue.is_queue_gated(max_backlog=args.max_proposal_backlog):
            msg = (
                f"Proposal queue backlog threshold reached ({queue.get_open_proposals_count()} >= {args.max_proposal_backlog}). "
                "Halting scheduled full-graph scout until operator review resolves pending proposals."
            )
            if args.json:
                print(json.dumps({"status": "QUEUE_GATED", "message": msg, "open_proposals": queue.get_open_proposals_count()}))
            else:
                print(f"[GRAPH-SCOUT] [GATED] {msg}")
            return 0

        # 2. Cadence gate: mutation count or elapsed days
        ledger = GraphMutationLedger(runtime_root / "graph_mutation_ledger.json")
        if not ledger.should_trigger_scheduled_scout(cadence_mutations=args.cadence_mutations, cadence_days=args.cadence_days):
            msg = (
                f"Scheduled scout not due (cumulative mutations={ledger.get_total_mutations()}, "
                f"threshold={args.cadence_mutations} mutations or {args.cadence_days} days)."
            )
            if args.json:
                print(json.dumps({"status": "SCHEDULED_NOT_DUE", "message": msg, "cumulative_mutations": ledger.get_total_mutations()}))
            else:
                print(f"[GRAPH-SCOUT] [NOT DUE] {msg}")
            return 0

    graph_path = _resolve_graph(runtime_root, args.graph)

    if not args.json:
        print(f"[GRAPH-SCOUT] Target Graph: {graph_path}")
        print(f"[GRAPH-SCOUT] Runtime Root: {runtime_root}")
        print("[GRAPH-SCOUT] Mode: Zero-Mutation Read-Only (PRAGMA query_only = ON)")
        print("[GRAPH-SCOUT] Scanning active graph...")

    scout = GraphScout(
        graph_path=graph_path,
        runtime_root=runtime_root,
        similarity_threshold=args.similarity_threshold,
        max_candidate_age_days=args.max_candidate_age_days,
    )

    report = scout.run(output_dir=args.output_dir)

    if args.scheduled:
        from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
        ledger = GraphMutationLedger(runtime_root / "graph_mutation_ledger.json")
        ledger.record_full_scan(run_id=report["run_id"])

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    summary = report["summary"]
    print("\n============================================================")
    print(f" GRAPH HEALTH SCOUT REPORT: {report['run_id']}")
    print("============================================================")
    print(f" Near-Duplicate Entities Found:  {summary['duplicate_entity_count']}")
    print(f" Temporal Contradictions Found:  {summary['contradiction_count']}")
    print(f" Orphan / Stale Clutter Items:   {summary['clutter_count']}")
    print(f" Sealed Proposals Generated:     {summary['proposals_generated']}")
    print(f" HITL Action Items (Delta < 15%): {summary['hitl_required_count']}")
    print(f" Zero-Mutation Verified:         {summary['zero_mutation_verified']}")
    print(f" Saved Report:                   {report['report_path']}")
    print("============================================================\n")

    if report["duplicate_entities"]:
        print("--- Near-Duplicate Entity Candidates ---")
        for f in report["duplicate_entities"][:5]:
            status = "[HITL]" if f["hitl_required"] else "[AUTO]"
            print(f" {status} '{f['canonical_name_a']}' <-> '{f['canonical_name_b']}' (sim={f['similarity_score']:.2f}, delta={f['margin_delta']:.2f})")

    if report["contradictions"]:
        print("\n--- Temporal Contradiction Candidates ---")
        for c in report["contradictions"][:5]:
            status = "[HITL]" if c["hitl_required"] else "[AUTO]"
            print(f" {status} Subject: {c['subject_name']} | Predicate: {c['predicate']} (delta={c['margin_delta']:.2f})")

    if report["clutter"]:
        print("\n--- Orphan & Clutter Candidates ---")
        for k in report["clutter"][:5]:
            print(f" [{k['finding_type'].upper()}] {k['label']} -> Action: {k['action_recommended']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
