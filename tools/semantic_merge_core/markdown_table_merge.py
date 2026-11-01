"""
3-way merge resolver for Markdown tables.

Reconciles non-colliding table row insertions using the primary key column (first column).
"""

from __future__ import annotations

from typing import Dict, List, Tuple


def _parse_table(text: str) -> Tuple[List[str], List[str], Dict[str, str], List[str]]:
    lines = text.strip().splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            header_idx = i
            break

    if header_idx == -1 or header_idx + 1 >= len(lines):
        return [], [], {}, lines

    prefix = lines[:header_idx]
    header = lines[header_idx]
    separator = lines[header_idx + 1]
    sep_clean = separator.replace("|", "").replace("-", "").replace(":", "").strip()
    if sep_clean or "-" not in separator or not separator.strip().startswith("|"):
        return [], [], {}, lines

    rows: Dict[str, str] = {}
    row_order: List[str] = []
    suffix_idx = len(lines)

    for i in range(header_idx + 2, len(lines)):
        line = lines[i]
        if not line.strip().startswith("|"):
            suffix_idx = i
            break
        # extract primary key (first column)
        parts = [p.strip() for p in line.strip().split("|")[1:-1]]
        if parts:
            pk = parts[0]
            rows[pk] = line
            row_order.append(pk)

    suffix = lines[suffix_idx:]
    return prefix, [header, separator], rows, suffix


def merge_markdown_tables(
    ancestor_text: str,
    ours_text: str,
    theirs_text: str,
) -> Tuple[bool, str]:
    prefix_a, meta_a, rows_a, suffix_a = _parse_table(ancestor_text)
    prefix_o, meta_o, rows_o, suffix_o = _parse_table(ours_text)
    prefix_t, meta_t, rows_t, suffix_t = _parse_table(theirs_text)

    if not meta_o or not meta_t:
        return False, ""

    # Reconcile rows: ours base + theirs added
    merged_rows = dict(rows_o)
    for pk, row_str in rows_t.items():
        if pk not in rows_a and pk not in rows_o:
            # added in theirs
            merged_rows[pk] = row_str
        elif pk in rows_o and pk in rows_t and rows_o[pk] != rows_t[pk]:
            # true conflict on same row
            return False, ""

    res_lines = list(prefix_o) + list(meta_o)
    for _pk, row_str in merged_rows.items():
        res_lines.append(row_str)
    res_lines.extend(suffix_o)

    return True, "\n".join(res_lines) + "\n"
