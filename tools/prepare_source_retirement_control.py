#!/usr/bin/env python3
"""Build a source-bound Markdown control package for one graph source retirement."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.source_retirement import (  # noqa: E402
    SOURCE_RETIREMENT_CONTROL_KEY,
    validate_source_retirement_control,
)
from tools.prepare_markdown_source_intake import prepare_package  # noqa: E402
from tools.validate_dual_pipeline_contracts import ContractError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-date", required=True)
    parser.add_argument("--source-location")
    parser.add_argument(
        "--classification",
        choices=["public", "internal", "confidential", "restricted"],
        default="internal",
    )
    parser.add_argument("--audience", action="append", dest="audiences", required=True)
    parser.add_argument(
        "--source-posture",
        choices=[
            "authoritative_internal",
            "governed_internal",
            "derivative_internal",
            "working_notes",
            "external_first_party",
            "external_partner",
            "external_independent",
            "external_contextual",
        ],
        default="governed_internal",
    )
    parser.add_argument("--authority-rank", type=int, default=9)
    parser.add_argument(
        "--anchor-entity", default="EDO graph correction"
    )
    parser.add_argument(
        "--anchor-stable-identifier", default="EDO-graph-correction"
    )
    parser.add_argument(
        "--anchor-context",
        default=(
            "Operator-approved correction scope. It removes only the exact "
            "obsolete records listed in the source-bound control and does not "
            "assert new delivery facts."
        ),
    )
    parser.add_argument(
        "--chunk-section",
        action="append",
        dest="chunk_sections",
        default=["Requested correction"],
        help="Exact level-two control-source section to retain; repeat as needed.",
    )
    args = parser.parse_args()
    try:
        with args.targets.open("r", encoding="utf-8") as handle:
            control = validate_source_retirement_control(json.load(handle))
        package = prepare_package(
            args.source,
            args.output,
            source_id=args.source_id,
            source_date=args.source_date,
            source_location=args.source_location,
            classification=args.classification,
            audiences=args.audiences,
            source_posture=args.source_posture,
            authority_rank=args.authority_rank,
            anchor_entity=args.anchor_entity,
            anchor_stable_identifier=args.anchor_stable_identifier,
            anchor_context=args.anchor_context,
            chunk_sections=args.chunk_sections,
            additional_extensions={SOURCE_RETIREMENT_CONTROL_KEY: control},
        )
    except (OSError, ValueError, json.JSONDecodeError, ContractError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(args.output.resolve()),
                "artifact_id": package["artifact_id"],
                "target_source_id": control["target_source_id"],
                "source_file_sha256": package["source"]["source_file_sha256"],
                "canonical_digest": package["canonical_digest"]["value"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
