#!/usr/bin/env python3
"""Mechanical validator for knowledge query output formatting.

Validates that query responses adhere to the Two-Part Seamless Query contract
(INV-QUERY-CLEAN-PROSE):
1. Narrative prose contains zero mid-sentence assertion hashes, raw snake_case IDs, or inline database paths.
2. If citations exist, a structured '### Evidence & Graph Lineage' section is present.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dual_pipeline.query_core.lineage_formatter import (
    LINEAGE_SECTION_HEADER,
    format_seamless_query_response,
    validate_seamless_prose,
)

SAMPLE_LEGACY_INVALID = """
### PartnerAdvisory Product Owner Definition
In PartnerAdvisory models, the PO owns the product vision ([`ast_ing_00112233445566778899aabbccddeeff`](file:///path/to/graph.sqlite)).
Project LENS ([`workstream_lens`](file:///path/to/graph.sqlite)) is led by Alex Sanchez.
"""

SAMPLE_THREE_COL_INVALID = """
### PartnerAdvisory Product Owner Definition
- In PartnerAdvisory models, the Product Owner owns the product vision [1].

### Evidence & Graph Lineage

| Ref | Graph Path | Verified Evidence |
| :--- | :--- | :--- |
| [1] | `Product Owner` | [`ast_ing_997d4d3f`](file:///path/to/graph.sqlite) |
"""

SAMPLE_SEAMLESS_VALID = """
### PartnerAdvisory Product Owner Definition
- In PartnerAdvisory models, the Product Owner owns the product vision.
- Project Lens is led by Alex Sanchez.

### Evidence & Graph Lineage

**Graph Traversal Summary:** 3 assertions evaluated across 2 entities and 1 distinct sources (HIGH (91%) overall confidence).

| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |
| :--- | :--- | :--- | :--- |
| [PartnerAdvisory Operating Models](file:///path/to/graph.sqlite) | Product Owner, Lens, Alex Sanchez | 3 verified assertions | HIGH (91%) |
"""


def validate_query_markdown(content: str) -> tuple[bool, list[str]]:
    """Validate that query markdown satisfies the seamless two-part layout and 4-column Confidence contract."""
    violations: list[str] = []
    if not content or not content.strip():
        return True, []

    # Split into narrative prose and lineage footer if present
    if LINEAGE_SECTION_HEADER in content:
        parts = content.split(LINEAGE_SECTION_HEADER, 1)
        narrative_prose = parts[0]
        footer = parts[1]
    else:
        narrative_prose = content
        footer = ""

    # Check 1: Narrative prose must have zero leaked assertion hashes or raw tokens
    is_valid_prose, prose_violations = validate_seamless_prose(narrative_prose)
    if not is_valid_prose:
        violations.extend(prose_violations)

    # Check 2: Narrative notes must not contain parenthetical example stuffing (e.g., ...)
    if re.search(r"\(\s*(?:e\.g\.|i\.e\.)\s*,?", narrative_prose):
        violations.append(
            "Narrative notes contain parenthetical example stuffing '(e.g., ...)'; use clean em-dash clauses instead."
        )

    # Check 3: If footer is present, verify Confidence and formatting standards
    if footer:
        if not re.search(r"\|\s*Confidence\s*\|", footer, re.IGNORECASE):
            violations.append(
                f"'{LINEAGE_SECTION_HEADER}' table is missing required 'Confidence' column."
            )
        if re.search(r"\|\s*Source\s*/\s*Domain\s*\|", footer, re.IGNORECASE) and not re.search(r"\*\*Graph Traversal Summary:\*\*", footer):
            violations.append(
                f"'{LINEAGE_SECTION_HEADER}' source rollup table is missing required '**Graph Traversal Summary:**' telemetry callout."
            )
        if re.search(r"\bHAS_CLAIM_TEXT\b", footer):
            violations.append(
                f"'{LINEAGE_SECTION_HEADER}' table Graph Path contains raw 'HAS_CLAIM_TEXT' predicate instead of human-readable relationship."
            )
        if re.search(r"\|\s*`?SUPERSEDED\b", footer, re.IGNORECASE):
            violations.append(
                f"'{LINEAGE_SECTION_HEADER}' table cites a SUPERSEDED assertion."
            )
        if re.search(r"\]\([^)]+\)\s*(?:—|--|-)\s*`?(?:ev_legacy_edge_|asrt_)", footer):
            violations.append(
                f"'{LINEAGE_SECTION_HEADER}' Verified Evidence column leaks raw database primary keys outside the clickable source link."
            )

    raw_groups = re.findall(r"\[([0-9,\s]+)\]", narrative_prose)
    footnote_refs = [idx.strip() for group in raw_groups for idx in group.split(",") if idx.strip().isdigit()]
    if footnote_refs:
        if not footer:
            violations.append(
                f"Narrative prose contains footnote references ({footnote_refs[:3]}...) but missing "
                f"'{LINEAGE_SECTION_HEADER}' footer section."
            )
        else:
            for ref_idx in set(footnote_refs):
                marker = f"[{ref_idx}]"
                if marker not in footer:
                    violations.append(
                        f"Footnote reference {marker} in narrative prose is missing from footer table."
                    )

    return len(violations) == 0, violations


def run_self_test() -> int:
    """Run internal validation suite on representative valid and invalid responses."""
    print("Running query output format validator self-test...")

    # 1. Verify invalid legacy response fails
    is_valid_inv, violations_inv = validate_query_markdown(SAMPLE_LEGACY_INVALID)
    if is_valid_inv or not violations_inv:
        print("[FAIL] Self-test failed: legacy invalid response was incorrectly marked valid!", file=sys.stderr)
        return 1

    # 2. Verify 3-column table without Confidence column fails
    is_valid_3col, violations_3col = validate_query_markdown(SAMPLE_THREE_COL_INVALID)
    if is_valid_3col or not any("Confidence" in v for v in violations_3col):
        print("[FAIL] Self-test failed: 3-column table without Confidence column was not rejected!", file=sys.stderr)
        return 1

    # 3. Verify seamless 4-column valid response passes
    is_valid_val, violations_val = validate_query_markdown(SAMPLE_SEAMLESS_VALID)
    if not is_valid_val or violations_val:
        print(f"[FAIL] Self-test failed: seamless valid response failed validation: {violations_val}", file=sys.stderr)
        return 1

    # 4. Verify formatter conversion output passes
    converted = format_seamless_query_response(SAMPLE_LEGACY_INVALID)
    is_valid_conv, violations_conv = validate_query_markdown(converted.formatted_markdown)
    if not is_valid_conv or violations_conv:
        print(f"[FAIL] Self-test failed: auto-formatted output failed validation: {violations_conv}", file=sys.stderr)
        return 1

    print("[PASS] Query output format validator self-test passed successfully (all checks verified).")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for validate_query_output_format."""
    parser = argparse.ArgumentParser(description="Validate query markdown output format.")
    parser.add_argument("--file", "-f", type=Path, help="Path to markdown file to validate.")
    parser.add_argument("--text", "-t", type=str, help="Raw text string to validate.")
    parser.add_argument("--self-test", action="store_true", help="Run validator self-tests.")

    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if args.file:
        if not args.file.exists():
            print(f"[ERROR] File not found: {args.file}", file=sys.stderr)
            return 1
        content = args.file.read_text(encoding="utf-8")
    elif args.text:
        content = args.text
    else:
        if sys.stdin.isatty():
            parser.print_help()
            return 1
        import select
        try:
            r, _, _ = select.select([sys.stdin], [], [], 5.0)
            content = sys.stdin.read() if r else ""
        except (OSError, ValueError):
            content = ""

    is_valid, violations = validate_query_markdown(content)
    if not is_valid:
        print(f"[FAIL] Query output format validation failed with {len(violations)} violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("[PASS] Query output format is compliant with INV-QUERY-CLEAN-PROSE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
