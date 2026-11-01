#!/usr/bin/env python3
"""Build the exact resource claims an approver must bind into a qualification JWT."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (  # noqa: E402
    digest,
    normalize_space,
    read_json,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_exclusive,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copilot-package", type=Path, required=True)
    parser.add_argument("--architecture-package", type=Path, required=True)
    parser.add_argument("--pdlc-question-file", type=Path, required=True)
    parser.add_argument("--copilot-decisions", type=Path)
    parser.add_argument("--architecture-decisions", type=Path)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    question = normalize_space(args.pdlc_question_file.read_text(encoding="utf-8"))
    model_manifest = read_json(args.model_manifest.resolve())
    declared_manifest_digest = model_manifest.get("manifest_digest")
    actual_manifest_digest = digest(
        {key: value for key, value in model_manifest.items() if key != "manifest_digest"}
    )
    if (
        model_manifest.get("schema_version") != "1.0.0"
        or model_manifest.get("model_id") != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
        or declared_manifest_digest != actual_manifest_digest
    ):
        parser.error("--model-manifest has an invalid identity or canonical digest")
    resources = {
        "copilot_package": sha256_file(args.copilot_package.resolve()),
        "architecture_package": sha256_file(args.architecture_package.resolve()),
        "pdlc_question": sha256_bytes(question.encode("utf-8")),
        "release_approval": sha256_bytes(args.approval_id.encode("utf-8")),
    }
    if args.copilot_decisions:
        resources["copilot_decisions"] = sha256_file(args.copilot_decisions.resolve())
    if args.architecture_decisions:
        resources["architecture_decisions"] = sha256_file(
            args.architecture_decisions.resolve()
        )
    value = {
        "schema_version": "1.0.0",
        "initiative_id": "NEE-KG-DUAL-PIPELINE-01",
        "created_at": utc_now(),
        "requested_capabilities": [
            "knowledge:intake",
            "knowledge:query",
            "knowledge:release",
            "knowledge:production-qualify",
        ],
        "nee_authorized_resources": dict(sorted(resources.items())),
        "nee_model_manifest_digest": actual_manifest_digest,
        "model_id": model_manifest["model_id"],
        "model_revision": model_manifest["model_revision"],
    }
    value["request_digest"] = digest(value)
    write_json_exclusive(args.output.resolve(), value)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
