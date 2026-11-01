#!/usr/bin/env python
"""Prepare a read-only, pair-bound W6 graph-label adjudication packet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import write_json_exclusive
from dual_pipeline.label_adjudication import build_graph_label_adjudication_packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a pending human-adjudication packet bound to the exact "
            "active graph/index pair. This tool never changes the graph, "
            "runtime, or active pair."
        )
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional immutable packet path; fails if that path already exists.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    packet = build_graph_label_adjudication_packet(
        args.graph,
        runtime_root=args.runtime_root,
    )
    if args.output:
        output = args.output.resolve()
        write_json_exclusive(output, packet)
        json.dump(
            {
                "status": packet["status"],
                "output": str(output),
                "canonical_digest": packet["canonical_digest"]["value"],
                "summary": packet["summary"],
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    else:
        json.dump(packet, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
