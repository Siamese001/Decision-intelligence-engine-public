"""
dual_pipeline/cli/formats_cmd.py

Command handler and CLI parser for 'nee formats'.
Inspects the Single Source of Truth capability registry, reporting supported,
partial, and blocked document and media formats.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from dual_pipeline.source_classification.capability_registry import (
    audit_matrix,
    get_capability_by_ext,
)


def add_formats_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'formats' subcommand parser."""
    parser = subparsers.add_parser(
        "formats",
        help="Inspect registered ingestion format capabilities, adapters, and support status.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output raw JSON format capability matrix.",
    )
    parser.add_argument(
        "--status",
        choices=["SUPPORTED", "PARTIAL", "BLOCKED", "UNAVAILABLE"],
        default=None,
        help="Filter format capabilities by support status.",
    )
    parser.add_argument(
        "--ext",
        type=str,
        default=None,
        help="Inspect details for a specific file extension (e.g. .docx, .xls).",
    )
    return parser


def handle_formats_command(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute 'nee formats' command."""
    matrix = audit_matrix()

    # Filter by extension if requested
    if getattr(args, "ext", None):
        cap = get_capability_by_ext(args.ext)
        if not cap:
            print(
                f"Extension '{args.ext}' is not registered in the format capability registry.",
                file=sys.stderr,
            )
            return {"status": "FAIL", "error": f"Unknown extension '{args.ext}'"}
        matrix = {cap.format_id: cap.to_dict()}

    # Filter by status if requested
    status_filter = getattr(args, "status", None)
    if status_filter:
        matrix = {k: v for k, v in matrix.items() if v["status"] == status_filter}

    if getattr(args, "json", False):
        print(json.dumps(matrix, indent=2))
        return {"status": "PASS", "formats": matrix}

    # Render formatted human-readable table
    print(f"\n{'Format ID':<15} {'Status':<12} {'Extensions':<30} {'Notes'}")
    print("-" * 90)
    for fid, item in sorted(matrix.items()):
        exts_str = ", ".join(item["extensions"])
        status_str = item["status"]
        notes_str = item.get("notes", "")[:45]
        print(f"{fid:<15} {status_str:<12} {exts_str:<30} {notes_str}")
    print("")

    return {"status": "PASS", "count": len(matrix), "formats": matrix}
