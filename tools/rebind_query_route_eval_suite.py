#!/usr/bin/env python3
"""Create a new current-policy binding for a sealed route-evaluation suite."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_file  # noqa: E402
from dual_pipeline.core import seal_artifact, write_json_exclusive  # noqa: E402
from dual_pipeline.query_router import load_query_routing_policy  # noqa: E402

ROUTE_SUITE_CONTRACT = "urn:nee:dual-pipeline:query-route-eval-suite:1.0.0"


def rebind_route_suite(
    *,
    source_path: Path,
    output_path: Path,
    suite_id: str,
    artifact_id: str,
    created_at: str,
) -> dict[str, Any]:
    """Bind unchanged route cases to the active policy in a new artifact."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if source == output:
        raise ValueError("route suite rebind output must differ from its source")
    suite = validate_file(source)
    if suite["contract_id"] != ROUTE_SUITE_CONTRACT:
        raise ValueError("route suite rebind source has the wrong contract")
    case_ids = [str(case["case_id"]) for case in suite["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("route suite rebind source has duplicate case IDs")

    rebound = copy.deepcopy(suite)
    rebound["artifact_id"] = artifact_id
    rebound["suite_id"] = suite_id
    rebound["created_at"] = created_at
    rebound["policy_digest"] = load_query_routing_policy().policy_digest
    rebound["extensions"] = {
        **rebound["extensions"],
        "com.partner_advisory.nee.rebound-from": {
            "suite_id": suite["suite_id"],
            "canonical_digest": suite["canonical_digest"]["value"],
            "policy_digest": suite["policy_digest"],
        },
    }
    rebound = seal_artifact(rebound)
    write_json_exclusive(output, rebound)
    validated = validate_file(output)
    return {
        "status": "PASS",
        "output_path": str(output),
        "suite_id": validated["suite_id"],
        "suite_digest": validated["canonical_digest"]["value"],
        "policy_digest": validated["policy_digest"],
        "case_count": len(validated["cases"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--created-at", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = rebind_route_suite(
        source_path=args.source,
        output_path=args.output,
        suite_id=args.suite_id,
        artifact_id=args.artifact_id,
        created_at=args.created_at,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
