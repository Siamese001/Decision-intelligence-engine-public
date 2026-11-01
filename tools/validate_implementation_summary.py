#!/usr/bin/env python3
"""
Mechanical enforcement validator for wave implementation summaries and walkthroughs.

Enforces governance rules defined in AGENTS.md and .agents/rules/wave-progression.md:
1. Every implementation summary, completion report, and walkthrough artifact
   MUST prominently feature the Wave Implementation & Completion Status Table:
   | Wave Number | Description / Scope | Completion Status | Runtime Receipt & Verification Gate |
2. Every declared wave must be itemized with:
   - Explicit wave number (Wave 1, Wave 2, ...)
   - Concrete non-empty description / scope of changes
   - Recognized completion status (✅ COMPLETE / ⏳ OPEN / 🚫 BLOCKED)
   - Runtime receipt or verification gate command
3. Freeform narrative-only summaries that omit or obscure wave completion statuses
   fail validation closed.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RE_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
RE_WAVE_NUM = re.compile(r"Wave\s+(\d+)", re.IGNORECASE)

VALID_STATUS_TOKENS = {
    "COMPLETE": ("complete", "pass", "passed", "done", "✅", "✔", "[x]"),
    "OPEN": ("open", "in_progress", "in progress", "pending", "⏳", "open / in progress", "[ ]"),
    "BLOCKED": ("blocked", "failed", "fail", "error", "halted", "🚫", "❌"),
}


def parse_markdown_tables(content: str) -> List[List[List[str]]]:
    """Extract all markdown tables using markdown-it-py AST token parser."""
    import markdown_it

    md = markdown_it.MarkdownIt("gfm-like")
    tokens = md.parse(content)

    tables: List[List[List[str]]] = []
    current_table: List[List[str]] = []
    current_row: List[str] = []
    in_cell = False

    for token in tokens:
        if token.type == "table_open":
            current_table = []
        elif token.type == "table_close":
            if current_table:
                tables.append(current_table)
            current_table = []
        elif token.type == "tr_open":
            current_row = []
        elif token.type == "tr_close":
            if current_row:
                current_table.append(current_row)
            current_row = []
        elif token.type in ("th_open", "td_open"):
            in_cell = True
        elif token.type in ("th_close", "td_close"):
            in_cell = False
        elif in_cell and token.type == "inline":
            current_row.append(token.content.strip())

    return tables


def is_wave_table_header(header_row: List[str]) -> bool:
    """Check if table headers match Wave Implementation Status Table schema."""
    if len(header_row) < 3:
        return False

    col1 = header_row[0].lower()
    col2 = header_row[1].lower()
    col3 = header_row[2].lower()

    has_wave = any(k in col1 for k in ("wave", "wave number", "wave #"))
    has_desc = any(k in col2 for k in ("desc", "scope", "milestone", "deliverable"))
    has_status = any(k in col3 for k in ("status", "completion", "state"))

    return has_wave and has_desc and has_status


def parse_status_token(status_cell: str) -> Optional[str]:
    """Categorize status string into COMPLETE, OPEN, BLOCKED, or None if unrecognized."""
    norm = status_cell.lower().strip()
    for cat, tokens in VALID_STATUS_TOKENS.items():
        if any(tok in norm for tok in tokens):
            return cat
    return None


def _validate_wave_row(
    idx: int, row: List[str], has_deliverable_col: bool, filename: str
) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    errors: List[str] = []
    if len(row) < 3:
        return [f"{filename}: Row {idx} has fewer than 3 columns."], None

    wave_cell = row[0]
    desc_cell = row[1]
    status_cell = row[2]
    deliverable_cell = row[3] if len(row) >= 5 else ""
    receipt_cell = row[4] if len(row) >= 5 else (row[3] if len(row) == 4 else "")

    # Validate Wave Number
    wave_match = RE_WAVE_NUM.search(wave_cell)
    wave_num_str = wave_match.group(1) if wave_match else None
    if not wave_num_str and not wave_cell.isdigit():
        errors.append(f"{filename}: Row {idx} has invalid wave identifier: '{wave_cell}' (expected 'Wave N').")

    # Validate Description
    clean_desc = re.sub(r"[*_`]", "", desc_cell).strip()
    if not clean_desc or len(clean_desc) < 5:
        errors.append(f"{filename}: Row {idx} description is too short or empty: '{desc_cell}'.")

    # Validate Status
    status_cat = parse_status_token(status_cell)
    if not status_cat:
        errors.append(
            f"{filename}: Row {idx} has unrecognized status: '{status_cell}'. "
            "Must clearly indicate COMPLETE (✅), OPEN (⏳), or BLOCKED (🚫)."
        )

    # Validate Deliverable for 5-column table on COMPLETE
    if has_deliverable_col and status_cat == "COMPLETE":
        clean_deliv = re.sub(r"[*_`]", "", deliverable_cell).strip()
        if not clean_deliv or len(clean_deliv) < 5:
            errors.append(
                f"{filename}: Row {idx} has status COMPLETE but lacks required Detailed Deliverable & Empirical Evidence: '{deliverable_cell}'."
            )

    rec = {
        "row_index": idx,
        "wave": wave_cell,
        "wave_number": int(wave_num_str) if wave_num_str else None,
        "description": clean_desc,
        "status": status_cat or "UNKNOWN",
        "raw_status": status_cell,
        "deliverable": deliverable_cell,
        "receipt": receipt_cell,
    }
    return errors, rec


def _check_trailing_contradictions(content: str, last_row_indicator: str, filename: str) -> List[str]:
    table_end_idx = content.rfind(last_row_indicator) if last_row_indicator else -1
    trailing_text = content[table_end_idx:] if table_end_idx != -1 else ""
    has_plan_ref = "implementation_plan.md" in trailing_text or "plans/" in trailing_text
    has_pending_phrase = any(
        phrase in trailing_text.lower()
        for phrase in (
            "awaiting your authorization to execute",
            "no code has been modified",
            "plan is in the implementation_plan",
        )
    )
    if has_plan_ref and has_pending_phrase:
        return [
            f"{filename}: Post-execution summary contains contradictory trailing plan artifact reference "
            "('implementation_plan.md' with pending execution language) after declaring all waves COMPLETE."
        ]
    return []


def is_concise_engineering_summary(content: str) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """Validate content as a concise engineering summary under engineering-output.md."""
    from tools.engineering_output import find_forbidden_process_sections, validate_text_limits

    errors: List[str] = []
    forbidden = find_forbidden_process_sections(content)
    if forbidden:
        errors.extend(forbidden)
        return False, errors, []

    limit_errs = validate_text_limits(content)
    if limit_errs:
        errors.extend(limit_errs)
        return False, errors, []

    norm = content.lower()
    has_action = any(term in norm for term in ("ran", "executed", "attempted", "tested", "built", "verified"))
    has_effect = any(term in norm for term in ("observed", "result", "passed", "failed", "output", "effect"))
    has_env = any(term in norm for term in ("local", "sandbox", "production"))

    if not (has_action and has_effect and has_env):
        errors.append(
            "Concise engineering summary must declare action/input, execution environment "
            "(local, sandbox, or production), and observed effect."
        )
        return False, errors, []

    wave_match = re.search(r"Wave\s+(\d+)", content, re.IGNORECASE)
    wave_num = int(wave_match.group(1)) if wave_match else None
    wave_label = f"Wave {wave_num}" if wave_num else "Engineering Completion"
    status = "COMPLETE" if not any(k in norm for k in ("failed", "blocked", "error")) else "BLOCKED"

    records = [{
        "row_index": 1,
        "wave": wave_label,
        "wave_number": wave_num,
        "description": "Concise engineering summary",
        "status": status,
        "raw_status": "CONCISE_SUMMARY",
        "deliverable": content.strip()[:100],
        "receipt": "internal_receipt",
    }]
    return True, [], records


def validate_summary_content(content: str, filename: str = "summary.md") -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """Validate markdown summary content against Wave Implementation Status Table schema or concise engineering summary.

    Returns:
        (is_compliant: bool, errors: list[str], wave_records: list[dict])
    """
    errors: List[str] = []
    wave_records: List[Dict[str, Any]] = []

    tables = parse_markdown_tables(content)
    candidate_tables = [t for t in tables if t and is_wave_table_header(t[0])]

    if not candidate_tables:
        is_concise, c_errs, c_recs = is_concise_engineering_summary(content)
        if is_concise:
            return True, [], c_recs
        errors.append(
            f"{filename}: Missing mandatory Wave Implementation & Completion Status Table "
            "or compliant concise engineering summary (target ~120 words, <=300 words, <=3500 chars, "
            "leading with action/input, environment, and observed effect)."
        )
        if c_errs:
            errors.extend(c_errs)
        return False, errors, []

    wave_table = candidate_tables[0]
    header_row = wave_table[0]
    data_rows = wave_table[1:]

    has_deliverable_col = len(header_row) >= 5 and any(
        k in header_row[3].lower() for k in ("deliverable", "evidence", "detail", "proof")
    )

    if not data_rows:
        errors.append(f"{filename}: Wave Implementation Table contains 0 wave rows.")
        return False, errors, []

    for idx, row in enumerate(data_rows, start=1):
        row_errors, rec = _validate_wave_row(idx, row, has_deliverable_col, filename)
        errors.extend(row_errors)
        if rec:
            wave_records.append(rec)

    all_complete = bool(wave_records and all(r["status"] == "COMPLETE" for r in wave_records))
    if all_complete:
        last_indicator = wave_table[-1][0] if wave_table else ""
        errors.extend(_check_trailing_contradictions(content, last_indicator, filename))

    is_compliant = (len(errors) == 0)
    return is_compliant, errors, wave_records


def _run_check_all(require_summary: bool, repo_root: Optional[Path] = None) -> int:
    """Scan and validate all walkthrough markdown files and active plans across repository."""
    from tools.validate_implementation_summary_core import run_check_all

    root = repo_root or Path(__file__).resolve().parents[1]
    return run_check_all(require_summary, root, validate_summary_content)


def _check_records(records: List[Dict[str, Any]], require_all_complete: bool) -> int:
    """Verify wave rows satisfy completion requirements."""
    if require_all_complete:
        non_complete = [r for r in records if r["status"] != "COMPLETE"]
        if non_complete:
            print(f"❌ VALIDATION FAILED: {len(non_complete)} wave(s) are not marked COMPLETE:")
            for r in non_complete:
                print(f"   - {r['wave']}: {r['status']} ({r['raw_status']})")
            return 1
        missing_receipts = [
            r for r in records
            if not r.get("receipt") or any(tok in str(r.get("receipt", "")).lower() for tok in ("pending", "none", "tbd", "todo", "n/a"))
        ]
        if missing_receipts:
            print(f"❌ VALIDATION FAILED: {len(missing_receipts)} wave(s) lack valid runtime receipts:")
            for r in missing_receipts:
                print(f"   - {r['wave']}: receipt='{r.get('receipt')}'")
            return 1

    print(f"✅ PASSED: Found {len(records)} valid wave rows in Wave Implementation Status Table:")
    for r in records:
        icon = "✅" if r["status"] == "COMPLETE" else ("⏳" if r["status"] == "OPEN" else "🚫")
        print(f"   {icon} {r['wave']}: {r['description']} [{r['status']}]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Wave Implementation Status Tables in summaries and walkthroughs.")
    parser.add_argument("positional_file", nargs="?", type=Path, help="Optional positional path to markdown summary or walkthrough file.")
    parser.add_argument("--file", "-f", type=Path, help="Path to markdown summary or walkthrough file.")
    parser.add_argument("--text", "-t", type=str, help="Raw markdown text to validate directly.")
    parser.add_argument("--require-all-complete", action="store_true", help="Require all wave rows to have status COMPLETE.")
    parser.add_argument("--check-all", action="store_true", help="Scan and validate all walkthrough markdown files across the repository.")
    parser.add_argument("--require-summary", action="store_true", help="Require at least one summary file when using --check-all.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Root directory to scan (defaults to repo root or cwd).")

    args = parser.parse_args()

    root = (args.repo_root or (Path.cwd() if (Path.cwd() / "plans").is_dir() and Path.cwd() != Path(__file__).resolve().parents[1] else Path(__file__).resolve().parents[1])).resolve()

    if args.check_all:
        return _run_check_all(args.require_summary, repo_root=root)

    target_file = args.file or args.positional_file
    if not target_file and not args.text:
        parser.error("Either a file path, --text, or --check-all must be provided.")

    filename = str(target_file.name) if target_file else "inline_text"
    if target_file:
        if not target_file.exists():
            print(f"❌ Error: File not found: {target_file}", file=sys.stderr)
            return 1
        content = target_file.read_text(encoding="utf-8")
    else:
        content = args.text or ""

    is_compliant, errors, records = validate_summary_content(content, filename=filename)
    if not is_compliant:
        print(f"❌ VALIDATION FAILED: {len(errors)} error(s) detected in implementation summary:")
        for err in errors:
            print(f"   - {err}")
        return 1

    return _check_records(records, args.require_all_complete)


if __name__ == "__main__":
    sys.exit(main())
