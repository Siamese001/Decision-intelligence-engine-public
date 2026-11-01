#!/usr/bin/env python3
"""
Custom Git Semantic Merge Driver.

Invoked by git during 3-way merge for registered file types:
  tools/semantic_merge_driver.py <ancestor> <ours> <theirs> <pathname>

Exit codes:
  0: Conflict resolved cleanly. Merged contents written to <ours>.
  1: Unresolvable conflict. Git will invoke standard conflict markers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.semantic_merge_core import (  # noqa: E402
    merge_json_registries,
    merge_markdown_tables,
    merge_python_ast_sets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Git semantic merge driver.")
    parser.add_argument("ancestor", help="Ancestor file (%O)")
    parser.add_argument("ours", help="Current branch file (%A)")
    parser.add_argument("theirs", help="Other branch file (%B)")
    parser.add_argument(
        "pathname", nargs="?", default="", help="File path relative to repo root (%P)"
    )
    args = parser.parse_args()

    ancestor_path = Path(args.ancestor)
    ours_path = Path(args.ours)
    theirs_path = Path(args.theirs)
    pathname = args.pathname or ours_path.name

    if not ours_path.exists() or not theirs_path.exists():
        return 1

    ancestor_text = ancestor_path.read_text(encoding="utf-8") if ancestor_path.exists() else ""
    ours_text = ours_path.read_text(encoding="utf-8")
    theirs_text = theirs_path.read_text(encoding="utf-8")

    success = False
    merged_text = ""

    # Route based on file type and name
    if pathname.endswith(".py") or "constants" in pathname:
        success, merged_text = merge_python_ast_sets(ancestor_text, ours_text, theirs_text)

    if not success and pathname.endswith(".json"):
        success, merged_text = merge_json_registries(ancestor_text, ours_text, theirs_text)

    if not success and pathname.endswith(".md"):
        success, merged_text = merge_markdown_tables(ancestor_text, ours_text, theirs_text)

    if success and merged_text:
        ours_path.write_text(merged_text, encoding="utf-8")
        print(f"[semantic-merge-driver] Successfully auto-resolved 3-way conflict in: {pathname}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
