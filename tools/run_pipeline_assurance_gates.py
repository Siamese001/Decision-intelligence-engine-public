#!/usr/bin/env python3
"""Fail closed if the release-assurance portfolio is incomplete."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.evals.portfolio import validate_portfolio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.repository_root.expanduser().resolve()
    report = validate_portfolio(root / "evals" / "suites" / "pipeline-assurance-portfolio-e2.json")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
