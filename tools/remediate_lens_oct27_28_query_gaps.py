#!/usr/bin/env python3
"""Governed CLI entry point for Project LENS October 27-28 candidate assertion promotion and sidecar sync."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.graph_writer_core import GovernedGraphWriter  # noqa: F401
from tools.tier2_dedup_core.lens_oct27_28_gap_remediation import (
    LENS_OCT27_28_CANONICAL_SOURCES,
    MICHAEL_REISS_NODE_ID,
    MICHAEL_RICE_NODE_ID,
    promote_lens_oct27_28_sources_and_assertions,
)

__all__ = [
    "GovernedGraphWriter",
    "LENS_OCT27_28_CANONICAL_SOURCES",
    "MICHAEL_REISS_NODE_ID",
    "MICHAEL_RICE_NODE_ID",
    "promote_lens_oct27_28_sources_and_assertions",
    "remediate_lens_oct27_28_query_gaps",
]


def remediate_lens_oct27_28_query_gaps(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute governed promotion of October 27-28 LENS operational assertions with 5-check physical proof."""
    return promote_lens_oct27_28_sources_and_assertions(
        runtime_root=runtime_root,
        receipt_path=receipt_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote verified October 27-28 Project LENS operational assertions and synchronize sidecar."
    )
    parser.add_argument("--runtime-root", type=Path, default=None, help="Optional runtime root directory.")
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path("reports/wave3_lens_promotion_sandbox_receipt.json"),
        help="Path to write the physical proof receipt JSON.",
    )
    args = parser.parse_args()
    receipt = remediate_lens_oct27_28_query_gaps(
        runtime_root=args.runtime_root,
        receipt_path=args.receipt,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt.get("terminal_outcome") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
