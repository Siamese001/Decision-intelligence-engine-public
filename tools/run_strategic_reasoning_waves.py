#!/usr/bin/env python3
"""Execute the shadow strategic-reasoning waves and emit chained receipts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.strategic_waves import run_strategic_waves  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--graph",
        type=Path,
        default=ROOT / "graph" / "nee_graph.sqlite",
    )
    parser.add_argument("--active-pair", type=Path)
    parser.add_argument("--completed-at")
    args = parser.parse_args()
    result = run_strategic_waves(
        output_root=args.output_root,
        graph_path=args.graph,
        active_pair_path=args.active_pair,
        root=ROOT,
        completed_at=args.completed_at,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
