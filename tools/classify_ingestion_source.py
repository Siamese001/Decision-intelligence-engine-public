#!/usr/bin/env python3
"""Create a provider-free pre-intake classification proposal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json, write_json_exclusive  # noqa: E402
from dual_pipeline.pre_intake_classifier import (  # noqa: E402
    PreIntakePolicyError,
    propose_pre_intake_classification,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--draft",
        type=Path,
        required=True,
        help="JSON package draft or source metadata envelope",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Canonical UTF-8 text extracted by the source-specific adapter",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="Versioned pre-intake classification policy; defaults to the checked-in policy",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        draft_path = args.draft.expanduser().resolve()
        source_path = args.source.expanduser().resolve()
        draft = read_json(draft_path)
        if not isinstance(draft, dict):
            raise ValueError("--draft must contain one JSON object")
        source_bytes = source_path.read_bytes()
        report = propose_pre_intake_classification(
            package=draft,
            source_bytes=source_bytes,
            policy_path=args.policy,
        )
        if args.output is not None:
            write_json_exclusive(args.output.expanduser().resolve(), report)
    except (OSError, ValueError, json.JSONDecodeError, PreIntakePolicyError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
