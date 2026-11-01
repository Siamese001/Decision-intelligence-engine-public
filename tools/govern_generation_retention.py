#!/usr/bin/env python3
"""CLI utility to audit and govern runtime generation retention."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dual_pipeline.retrieval_release_core.retention import (
    enforce_generation_retention,
    evaluate_generation_retention,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit and govern runtime generation retention.")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Explicit runtime root")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Main repository root")
    parser.add_argument("--keep-releases", type=int, default=5, help="Number of releases to retain")
    parser.add_argument(
        "--apply", action="store_true", default=False, help="Apply move to archived_generations"
    )
    args = parser.parse_args(argv)

    if args.apply:
        result = enforce_generation_retention(
            runtime_root=args.runtime_root,
            repo_root=args.repo_root,
            keep_releases=args.keep_releases,
            dry_run=False,
        )
    else:
        result = evaluate_generation_retention(
            runtime_root=args.runtime_root,
            repo_root=args.repo_root,
            keep_releases=args.keep_releases,
        )
        result["dry_run"] = True

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
