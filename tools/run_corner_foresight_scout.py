#!/usr/bin/env python3
"""Run the Governed Corner Foresight & Multi-Hop Blind-Spot Radar Scout.

Usage:
    .venv/bin/python tools/run_corner_foresight_scout.py [--runtime-root <path>] [--graph <path>] [--output-dir <path>] [--radar-date <MM/DD/YYYY>]

Enforces strict read-only zero-mutation execution (PRE_GRAPH_SHA256 == POST_GRAPH_SHA256)
while traversing 1-to-3 hop causal domino chains, entity redirects, stakeholder velocity
cohorts, and single-source bridge fragility across the canonical Knowledge Graph.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scheduled_task_core.adapters_core.foresight_engine import (
    CornerForesightGraphEngine,
    render_foresight_radar_markdown,
    resolve_target_graph_db,
)


def _common_repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return Path(out).resolve().parent
    except Exception:
        pass
    return ROOT


def _resolve_graph_path(runtime_root: Path, explicit_graph: Path | None) -> Path:
    if explicit_graph and explicit_graph.is_file():
        return explicit_graph.resolve()

    pair_candidates = [
        runtime_root / "active_pair.json",
        ROOT / ".local" / "dual_pipeline" / "active_pair.json",
        _common_repo_root() / ".local" / "dual_pipeline" / "active_pair.json",
    ]
    for pair_path in pair_candidates:
        if pair_path.is_file():
            try:
                pair = json.loads(pair_path.read_text(encoding="utf-8"))
                graph_rel = pair.get("graph_path")
                if graph_rel:
                    resolved = (pair_path.parent / graph_rel).resolve()
                    if resolved.is_file():
                        return resolved
            except Exception:
                pass

    found = resolve_target_graph_db(ROOT)
    if found and found.is_file():
        return found.resolve()

    raise FileNotFoundError(f"Cannot resolve active Knowledge Graph from runtime root: {runtime_root}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Governed Look-Around-the-Corner Multi-Hop Graph Foresight Scout"
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
        help="Explicit path to SQLite Knowledge Graph database",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "corner_foresight",
        help="Directory to write foresight radar JSON and Markdown brief",
    )
    parser.add_argument(
        "--radar-date",
        type=str,
        default="10/30/2026",
        help="Radar date in MM/DD/YYYY format",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output to stdout",
    )
    args = parser.parse_args(argv)

    graph_path = _resolve_graph_path(args.runtime_root.resolve(), args.graph)
    engine = CornerForesightGraphEngine(graph_path=graph_path, workspace_root=ROOT)
    report = engine.run_discovery()

    if not report.get("zero_mutation_verified"):
        print("[FAIL] Zero-mutation check failed: PRE_GRAPH_SHA256 != POST_GRAPH_SHA256", file=sys.stderr)
        return 1

    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    date_slug = args.radar_date.replace("/", "-")
    json_path = out_dir / f"radar_{date_slug}_report.json"
    md_path = out_dir / f"radar_{date_slug}.md"

    md_content = render_foresight_radar_markdown(report, radar_date=args.radar_date)
    md_path.write_text(md_content, encoding="utf-8")
    report["markdown_radar_path"] = str(md_path)
    report["json_report_path"] = str(json_path)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    tel = report["graph_traversal_telemetry"]
    print(f"[CORNER-FORESIGHT] Target Graph: {graph_path}")
    print(f"[CORNER-FORESIGHT] Nodes Traversed: {tel['nodes_traversed']} | Assertions Evaluated: {tel['canonical_assertions_evaluated']}")
    print(f"[CORNER-FORESIGHT] Causal Edges Indexed: {tel['causal_edges_indexed']} | Multi-Hop Paths Traced: {tel['multi_hop_paths_traced']}")
    print(f"[CORNER-FORESIGHT] Blind Spots Surfaced: {len(report['blind_spots'])} | Zero-Mutation Verified: {report['zero_mutation_verified']}")
    print(f"[CORNER-FORESIGHT] Radar Brief Written: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
