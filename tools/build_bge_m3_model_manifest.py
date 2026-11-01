#!/usr/bin/env python3
"""Build the exact local-file manifest required by the BGE-M3 backend."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, write_json  # noqa: E402
from dual_pipeline.index import is_link_or_reparse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    supplied_model_root = args.model_path.expanduser()
    if is_link_or_reparse(supplied_model_root):
        parser.error("--model-path cannot be a link or reparse point")
    model_root = supplied_model_root.resolve()
    output = args.output.expanduser().resolve()
    if not model_root.is_dir():
        parser.error("--model-path must be a local directory")
    if not re.fullmatch(r"[0-9a-f]{40}", args.model_revision):
        parser.error("--model-revision must be the immutable 40-hex model commit")
    try:
        output.relative_to(model_root)
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the immutable model snapshot")
    files = []
    for path in sorted(model_root.rglob("*")):
        if is_link_or_reparse(path):
            parser.error(
                f"model snapshot contains a link or reparse point: {path.relative_to(model_root)}"
            )
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(model_root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    files.sort(key=lambda item: PureWindowsPath(item["path"]))
    value = {
        "schema_version": "1.0.0",
        "model_id": "BAAI/bge-m3",  # noqa: SSOT_EXEMPT
        "model_revision": args.model_revision,
        "files": files,
    }
    value["manifest_digest"] = digest(value)
    write_json(output, value)
    summary = {
        "status": "PASS",
        "output": str(output),
        "file_count": len(files),
        "manifest_digest": value["manifest_digest"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
