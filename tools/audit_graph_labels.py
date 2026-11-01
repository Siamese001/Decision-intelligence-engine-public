#!/usr/bin/env python
"""Audit graph labels or emit a deterministic legacy migration plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import write_json_exclusive
from dual_pipeline.label_quality import (
    audit_graph_labels,
    build_deterministic_label_migration_candidate_plan,
    build_legacy_label_migration_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only graph-label audit and migration planning. "
            "This tool never mutates the graph."
        )
    )
    parser.add_argument("mode", choices=("audit", "plan", "candidate-plan"))
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail the audit when blocking legacy debt remains.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.mode == "audit":
        result = audit_graph_labels(args.graph, strict=args.strict)
    else:
        if args.strict:
            raise SystemExit("--strict is valid only for audit mode")
        result = (
            build_legacy_label_migration_plan(args.graph)
            if args.mode == "plan"
            else build_deterministic_label_migration_candidate_plan(args.graph)
        )
    if args.output:
        write_json_exclusive(args.output.resolve(), result)
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 1 if result.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
