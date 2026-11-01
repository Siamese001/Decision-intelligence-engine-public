"""Lint and enforce epistemic boundaries across knowledge graph and intake pipelines.

Cardinal Invariant:
Card prose is NEVER an input or evidence into the Knowledge Graph.
Cards are read-only, access-filtered saved view projections materialized
dynamically from verified graph assertions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json


def lint_epistemic_boundaries(
    *,
    runtime_root: Path | None = None,
    graph_path: Path | None = None,
    repo_root: Path | None = None,
    check_intake_packages: bool = True,
    strict_legacy: bool = False,
) -> dict[str, Any]:
    """Audit the active graph and intake packages for forbidden card-prose evidence."""
    root = (repo_root or Path(__file__).resolve().parent.parent).resolve()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    # 1. Resolve graph path from active_pair.json if runtime_root provided
    resolved_graph: Path | None = None
    if graph_path:
        resolved_graph = Path(graph_path).resolve()
    elif runtime_root:
        active_pair_path = Path(runtime_root) / "active_pair.json"
        if active_pair_path.is_file():
            active_pair = read_json(active_pair_path)
            raw_path = active_pair.get("graph_path")
            if raw_path:
                candidate = Path(runtime_root) / raw_path
                if candidate.is_file():
                    resolved_graph = candidate.resolve()

    if not resolved_graph:
        default_seed = root / "graph" / "nee_graph.sqlite"
        if default_seed.is_file():
            resolved_graph = default_seed

    scanned_sources = 0
    scanned_evidence = 0

    if resolved_graph and resolved_graph.is_file():
        con = create_sqlite_conn(resolved_graph, readonly=True)
        try:
            tables = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "source" in tables:
                cols = [r[1] for r in con.execute("PRAGMA table_info(source)").fetchall()]
                location_col = (
                    "canonical_location"
                    if "canonical_location" in cols
                    else "cold_archive_ref" if "cold_archive_ref" in cols else None
                )
                query = f"SELECT source_id, source_type, source_name{', ' + location_col if location_col else ''} FROM source"
                for row in con.execute(query).fetchall():
                    scanned_sources += 1
                    loc = str(row[location_col] or "") if location_col else ""
                    name = str(row["source_name"] or "")
                    source_id = str(row["source_id"] or "")
                    is_card_pointer = (
                        "cards/NEE_" in loc
                        or loc.startswith("cards/")
                        or (name.startswith("NEE_") and ".md" in name)
                        or ("_cards_" in loc and ".md" in loc)
                    )
                    if is_card_pointer:
                        is_cold_archive = "cold_archive://" in loc or source_id.startswith("source_v17_") or source_id.startswith("source_v23_")
                        entry = {
                            "type": "LEGACY_COLD_ARCHIVE_CARD_SOURCE" if is_cold_archive else "ACTIVE_CARD_SOURCE_VIOLATION",
                            "source_id": source_id,
                            "source_name": name,
                            "location": loc,
                            "severity": "ERROR" if (strict_legacy or not is_cold_archive) else "WARNING",
                            "reason": (
                                "Historical legacy v17-v23 source archive pointer to markdown card"
                                if is_cold_archive
                                else "Active source points to card markdown prose as raw evidence"
                            ),
                        }
                        if entry["severity"] == "ERROR":
                            errors.append(entry)
                        else:
                            warnings.append(entry)

            if "evidence_item" in tables:
                cols = [
                    r[1]
                    for r in con.execute("PRAGMA table_info(evidence_item)").fetchall()
                ]
                if "locator_json" in cols:
                    for row in con.execute(
                        "SELECT evidence_id, source_id, locator_json FROM evidence_item"
                    ).fetchall():
                        scanned_evidence += 1
                        loc_str = str(row["locator_json"] or "")
                        if "cards/NEE_" in loc_str or "cards\\" in loc_str:
                            errors.append({
                                "type": "GRAPH_EVIDENCE_CIRCULARITY",
                                "evidence_id": row["evidence_id"],
                                "source_id": row["source_id"],
                                "locator": loc_str,
                                "severity": "ERROR",
                                "reason": "Evidence locator references card markdown file",
                            })
        finally:
            con.close()

    # 2. Check intake directory for packages targeting cards/
    scanned_packages = 0
    if check_intake_packages:
        intake_dir = root / "intake"
        if intake_dir.is_dir():
            for pkg in intake_dir.glob("*.json"):
                scanned_packages += 1
                try:
                    data = json.loads(pkg.read_text(encoding="utf-8"))
                    text = json.dumps(data)
                    # Check for direct card intake attempt
                    if '"cards/' in text or '"cards\\' in text:
                        errors.append({
                            "type": "INTAKE_PACKAGE_CIRCULARITY",
                            "package_path": str(pkg),
                            "severity": "ERROR",
                            "reason": "Intake package references cards/ directory in payload or source locator",
                        })
                except Exception:
                    pass

    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "graph_inspected": str(resolved_graph) if resolved_graph else None,
        "scanned_sources_count": scanned_sources,
        "scanned_evidence_items_count": scanned_evidence,
        "scanned_packages_count": scanned_packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Knowledge Graph and intake pipelines for card-prose circularity."
    )
    parser.add_argument("--runtime-root", type=Path, default=None, help="Runtime root")
    parser.add_argument("--graph-path", type=Path, default=None, help="Explicit SQLite graph path")
    parser.add_argument("--strict-legacy", action="store_true", help="Fail even on historical v17 cold-archive pointers")
    parser.add_argument("--no-intake-packages", action="store_true", help="Skip intake package scanning")
    args = parser.parse_args()

    report = lint_epistemic_boundaries(
        runtime_root=args.runtime_root,
        graph_path=args.graph_path,
        strict_legacy=args.strict_legacy,
        check_intake_packages=not args.no_intake_packages,
    )

    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
