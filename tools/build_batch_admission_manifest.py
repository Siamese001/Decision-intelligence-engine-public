#!/usr/bin/env python3
"""Create an immutable, source-bound intake batch admission manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.batch_admission import create_manifest  # noqa: E402
from dual_pipeline.core import write_json_exclusive  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--package", type=Path, action="append", required=True)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument("--policy-version", required=True)
    parser.add_argument(
        "--admission-mode", choices=("HITL_REQUIRED", "SIGNED_POLICY"), default="HITL_REQUIRED"
    )
    parser.add_argument("--permitted-source-posture", action="append", default=[])
    parser.add_argument("--permitted-source-location-prefix", action="append", default=[])
    parser.add_argument("--max-package-count", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = create_manifest(
        artifact_id=args.artifact_id,
        packages=args.package,
        policy_id=args.policy_id,
        policy_version=args.policy_version,
        admission_mode=args.admission_mode,
        permitted_source_postures=args.permitted_source_posture or None,
        permitted_source_location_prefixes=args.permitted_source_location_prefix,
        max_package_count=args.max_package_count,
    )
    write_json_exclusive(args.output.resolve(), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
