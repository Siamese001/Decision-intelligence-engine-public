#!/usr/bin/env python
"""Read-only audit of the W2 normalized semantic spine."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(graph: Path, policy_path: Path) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    predicate_mapping = policy["predicate_mapping"]
    node_domains = policy["node_domains"]
    known_node_types = {
        node_type
        for domain in node_domains.values()
        for node_type in domain["current_node_types"]
    }
    future_node_types = set(node_domains["decision_intelligence_target"]["target_node_types"])
    connection = create_sqlite_conn(str(graph), readonly=True)
    try:
        node_rows = connection.execute(
            "SELECT node_type, count(*) AS n FROM node GROUP BY node_type ORDER BY node_type"
        ).fetchall()
        predicate_rows = connection.execute(
            "SELECT predicate, count(*) AS n FROM assertion GROUP BY predicate ORDER BY predicate"
        ).fetchall()
        legacy_assertion_count = int(
            connection.execute(
                "SELECT count(*) FROM assertion WHERE assertion_type='legacy_claim_record' OR atomicity_state='unparsed_legacy_text'"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    node_counts = {str(row["node_type"]): int(row["n"]) for row in node_rows}
    predicate_counts = {str(row["predicate"]): int(row["n"]) for row in predicate_rows}
    unknown_nodes = sorted(set(node_counts) - known_node_types)
    unknown_predicates = sorted(set(predicate_counts) - set(predicate_mapping))
    compound_predicates = sorted(
        predicate
        for predicate, definition in predicate_mapping.items()
        if definition["handling"] == "split" and predicate in predicate_counts
    )
    generic_predicates = sorted(
        predicate
        for predicate, definition in predicate_mapping.items()
        if definition["handling"] in {"provenance_only", "quarantine_for_review"}
        and predicate in predicate_counts
    )
    missing_target_nodes = sorted(future_node_types - set(node_counts))
    blockers = {
        "unknown_node_type_count": len(unknown_nodes),
        "unknown_predicate_count": len(unknown_predicates),
        "compound_predicate_count": len(compound_predicates),
        "generic_or_provenance_predicate_count": len(generic_predicates),
        "legacy_or_unparsed_assertion_count": legacy_assertion_count,
        "missing_target_node_type_count": len(missing_target_nodes),
    }
    blockers = {key: value for key, value in blockers.items() if value}
    return {
        "schema_version": "1.0",
        "artifact_type": "decision_intelligence_semantic_spine_audit",
        "contract_id": policy["contract_id"],
        "contract_version": policy["contract_version"],
        "graph_path": str(graph),
        "graph_sha256": _sha256(graph),
        "policy_path": str(policy_path),
        "status": "FAIL" if blockers else "PASS",
        "expected_w2_baseline": "FAIL",
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "node_type_counts": node_counts,
        "predicate_counts": predicate_counts,
        "mapped_predicate_count": len(set(predicate_counts) & set(predicate_mapping)),
        "current_predicate_count": len(predicate_counts),
        "unknown_node_types": unknown_nodes,
        "unknown_predicates": unknown_predicates,
        "compound_predicates": compound_predicates,
        "generic_or_provenance_predicates": generic_predicates,
        "missing_target_node_types": missing_target_nodes,
        "legacy_or_unparsed_assertion_count": legacy_assertion_count,
        "blockers": blockers,
        "interpretation": (
            "The semantic spine is defined and every current predicate has a "
            "disposition. Current legacy, compound, generic, and missing-target "
            "debt remains migration work and is not silently normalized."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "governance" / "decision_intelligence" / "W2_SEMANTIC_SPINE_CONTRACT.json",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = audit(args.graph.resolve(), args.policy.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
