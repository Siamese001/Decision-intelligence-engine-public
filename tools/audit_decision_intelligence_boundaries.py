#!/usr/bin/env python
"""Read-only W1 truth-lane and audience-boundary audit.

This audit measures the current graph against the W1 boundary contract. It
does not rewrite rows, alter indexes, call a provider, or prepare a release.
The expected W1 baseline result is FAIL while known boundary debt remains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


AUDIENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(connection: Any, query: str, params: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(query, params).fetchone()[0])


def _audiences(raw: str) -> tuple[list[str] | None, str | None]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(value, list) or not value:
        return None, "not_nonempty_array"
    if any(
        not isinstance(item, str)
        or item != item.strip()
        or not AUDIENCE_PATTERN.fullmatch(item)
        for item in value
    ):
        return None, "invalid_label"
    return sorted(set(value)), None


def audit(graph: Path, policy_path: Path) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    registry = policy["audience_registry"]
    known = set(registry)
    client_exposures = {"client_internal", "client_external"}
    restricted_exposures = {"partner_advisory_restricted", "private"}
    derived_types = {"human_interpretation", "recommendation", "hypothesis", "pov"}

    connection = create_sqlite_conn(str(graph), readonly=True)
    try:
        rows = connection.execute(
            """SELECT assertion_id, assertion_type, canonical_status,
                      canonical_eligible, retrieval_eligible, classification,
                      allowed_audiences_json, primary_source_id, extraction_run_id
                 FROM assertion
                ORDER BY assertion_id"""
        ).fetchall()
        counts = {
            "assertions": len(rows),
            "derived_in_canonical_retrieval_lane": 0,
            "derived_in_canonical_lane": 0,
            "unknown_audience_count": 0,
            "invalid_audience_count": 0,
            "client_private_or_restricted_mixed_count": 0,
            "confidential_client_audience_count": 0,
            "canonical_missing_primary_source_count": 0,
            "canonical_missing_extraction_lineage_count": 0,
        }
        examples: dict[str, list[str]] = {key: [] for key in counts if key != "assertions"}
        for row in rows:
            assertion_id = str(row["assertion_id"])
            parsed, error = _audiences(row["allowed_audiences_json"])
            if error:
                counts["invalid_audience_count"] += 1
                examples["invalid_audience_count"].append(assertion_id)
                parsed = []
            unknown = sorted(set(parsed or ()) - known)
            if unknown:
                counts["unknown_audience_count"] += 1
                examples["unknown_audience_count"].append(assertion_id)
            exposures = {registry[item]["exposure"] for item in (parsed or ()) if item in registry}
            if exposures & client_exposures and exposures & restricted_exposures:
                counts["client_private_or_restricted_mixed_count"] += 1
                examples["client_private_or_restricted_mixed_count"].append(assertion_id)
            if row["classification"] == "confidential" and exposures & client_exposures:
                counts["confidential_client_audience_count"] += 1
                examples["confidential_client_audience_count"].append(assertion_id)
            if row["assertion_type"] in derived_types and row["canonical_status"] == "canonical":
                counts["derived_in_canonical_lane"] += 1
                examples["derived_in_canonical_lane"].append(assertion_id)
                if row["retrieval_eligible"] == 1:
                    counts["derived_in_canonical_retrieval_lane"] += 1
                    examples["derived_in_canonical_retrieval_lane"].append(assertion_id)
            if row["canonical_status"] == "canonical" and not row["primary_source_id"]:
                counts["canonical_missing_primary_source_count"] += 1
                examples["canonical_missing_primary_source_count"].append(assertion_id)
            if (
                row["canonical_status"] == "canonical"
                and not row["extraction_run_id"]
                and row["assertion_type"] not in {"legacy_claim_record"}
            ):
                counts["canonical_missing_extraction_lineage_count"] += 1
                examples["canonical_missing_extraction_lineage_count"].append(assertion_id)
    finally:
        connection.close()

    blockers = {
        key: value
        for key, value in counts.items()
        if key != "assertions" and value
    }
    return {
        "schema_version": "1.0",
        "artifact_type": "decision_intelligence_boundary_audit",
        "contract_id": policy["contract_id"],
        "contract_version": policy["contract_version"],
        "graph_path": str(graph),
        "graph_sha256": _sha256(graph),
        "policy_path": str(policy_path),
        "status": "FAIL" if blockers else "PASS",
        "expected_w1_baseline": "FAIL",
        "provider_calls": 0,
        "graph_mutations": 0,
        "release_activation": False,
        "counts": counts,
        "blockers": blockers,
        "examples": {key: values[:10] for key, values in examples.items() if values},
        "interpretation": (
            "Boundary policy is established, but the current graph still contains "
            "known lane/audience debt. This audit is evidence for a future "
            "approval-bound migration; it does not authorize one."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "governance" / "decision_intelligence" / "W1_BOUNDARY_CONTRACT.json",
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
