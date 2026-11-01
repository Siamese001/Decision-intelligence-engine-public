#!/usr/bin/env python3
"""Build exact signed-resource claims for release activation or rollback."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, read_json, utc_now, write_json_exclusive  # noqa: E402
from dual_pipeline.release import release_action_resources  # noqa: E402
from dual_pipeline.retrieval_release import validate_activation_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--action", choices=("activate", "rollback"), required=True)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help=(
            "Runtime containing the prepared release and its retrieval-evaluation "
            "receipt; defaults to the receipt's parent runtime."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    receipt_path = args.receipt.resolve()
    receipt = read_json(receipt_path)
    resources = release_action_resources(receipt, args.action)
    retrieval_evaluation = None
    if args.action == "activate":
        runtime_root = (
            args.runtime_root.resolve()
            if args.runtime_root is not None
            else receipt_path.parent.parent.resolve()
        )
        # This uses the exact same receipt/pair/policy binding check as
        # ReleaseManager.activate.  A BGE-M3 activation request is therefore
        # not signable until the sealed PASS evaluation exists, and its digest
        # becomes part of the new action token's resource scope.
        retrieval_evaluation = validate_activation_evaluation(runtime_root, receipt)
        if retrieval_evaluation["required"]:
            resources["retrieval_release_evaluation"] = retrieval_evaluation[
                "evaluation_digest"
            ]
    model_manifest_digest = receipt.get("next_pair", {}).get(
        "model_manifest_digest"
    )
    if receipt.get("production_qualified") and not model_manifest_digest:
        parser.error("production release receipt has no model-manifest binding")
    request = {
        "schema_version": "1.0.0",
        "initiative_id": "NEE-KG-DUAL-PIPELINE-01",
        "request_type": "release-action-authorization",
        "created_at": utc_now(),
        "action": args.action,
        "receipt_id": receipt["receipt_id"],
        "generation_id": receipt["next_pair"]["generation_id"],
        "requested_capabilities": ["knowledge:release"],
        "nee_authorized_resources": resources,
        "nee_model_manifest_digest": model_manifest_digest,
        "retrieval_release_evaluation": retrieval_evaluation,
    }
    request["request_digest"] = digest(request)
    write_json_exclusive(args.output.resolve(), request)
    print(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
