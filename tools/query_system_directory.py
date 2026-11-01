#!/usr/bin/env python3
"""Governed CLI utility for querying the Apex Global Enterprise System Directory.

Provides deterministic lookup, project attribution, and writeback thesis
retrieval for enterprise source systems (LAMS, Maximo, SuccessFactors, etc.).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List

from dual_pipeline.system_directory import (
    find_systems_in_text,
    get_all_systems,
    get_systems_for_project,
    lookup_system,
)


def _format_human(system: Dict[str, Any]) -> str:
    """Formats a single system record for human-readable terminal output."""
    wb_text = (
        "YES (Active Writeback Supported)"
        if system.get("writeback_support")
        else f"NO ({system.get('writeback_posture', 'READ_ONLY')})"
    )
    lines = [
        "================================================================================",
        f"  Enterprise System: {system['canonical_name']} ({system['acronym']})",
        "================================================================================",
        f"  System ID:         {system['system_id']}",
        f"  Governing Project: {system['primary_governing_project']}",
        f"  System of Record:  {system['system_of_record_for']}",
        f"  Domain Scope:      {system['domain_scope']}",
        f"  Integration:       {system['integration_posture']}",
        f"  Writeback Support: {wb_text}",
        f"  Writeback Thesis:  {system['writeback_thesis']}",
        f"  Citations:         {', '.join(system.get('key_evidence_refs', [])) or 'N/A'}",
        "================================================================================",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query the Apex Global Enterprise System Directory for governance and routing."
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        help="Query text or system acronym to lookup (e.g., 'LAMS', 'Maximo', 'LAMS writeback').",
    )
    parser.add_argument(
        "--project", "-p",
        type=str,
        help="Filter systems by governing project (e.g., 'project_nexus', 'project_lens').",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="List all registered enterprise systems.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON.",
    )

    args = parser.parse_args()
    start_time = time.perf_counter()

    matched_systems: List[Dict[str, Any]] = []

    if args.all:
        all_sys = get_all_systems()
        matched_systems = [sys.to_dict() for sys in all_sys.values()]
    elif args.project:
        proj_sys = get_systems_for_project(args.project)
        matched_systems = [sys.to_dict() for sys in proj_sys]
    elif args.query:
        exact = lookup_system(args.query)
        if exact:
            matched_systems = [exact.to_dict()]
        else:
            discovered = find_systems_in_text(args.query)
            matched_systems = [sys.to_dict() for sys in discovered]
    else:
        parser.print_help()
        return 1

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    if args.json_output:
        result: Dict[str, Any] = {
            "status": "ok" if matched_systems else "not_found",
            "query": args.query,
            "project_filter": args.project,
            "count": len(matched_systems),
            "elapsed_ms": round(elapsed_ms, 3),
            "systems": matched_systems,
        }
        if len(matched_systems) == 1:
            top = matched_systems[0]
            result["governing_project"] = top["governing_project"]
            result["canonical_name"] = top["canonical_name"]
            result["acronym"] = top["acronym"]
            result["writeback_support"] = top["writeback_support"]
            result["writeback_thesis"] = top["writeback_thesis"]

        print(json.dumps(result, indent=2))
        return 0

    if not matched_systems:
        print(f"No enterprise systems matched query '{args.query or args.project}'.")
        return 0

    for sys_dict in matched_systems:
        print(_format_human(sys_dict))

    print(f"\nRetrieved {len(matched_systems)} system(s) in {elapsed_ms:.2f}ms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
