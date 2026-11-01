"""
Hunk parsing, normalization, and overlap detection against common merge base.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Tuple

from .models import FileHunkDiff, HunkRange

RE_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
RE_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)


def parse_unified_diff_u0(diff_text: str) -> Dict[str, FileHunkDiff]:
    """
    Parses a `git diff -U0` output into a dictionary of path -> FileHunkDiff.
    Extracts exact hunk ranges and computes whitespace-normalized hunk hashes.
    """
    result: Dict[str, FileHunkDiff] = {}
    current_file: Optional[FileHunkDiff] = None
    current_hunk: Optional[HunkRange] = None
    current_hunk_lines: List[str] = []

    def flush_hunk():
        nonlocal current_hunk, current_hunk_lines
        if current_file is not None and current_hunk is not None:
            current_file.hunks.append(current_hunk)
            hunk_hash = compute_normalized_hunk_hash(current_hunk_lines)
            current_file.hunk_hashes.append(hunk_hash)
        current_hunk = None
        current_hunk_lines = []

    def flush_file():
        nonlocal current_file
        flush_hunk()
        if current_file is not None:
            result[current_file.path] = current_file
        current_file = None

    for line in diff_text.splitlines():
        diff_match = RE_DIFF_HEADER.match(line)
        if diff_match:
            flush_file()
            old_p, new_p = diff_match.group(1), diff_match.group(2)
            current_file = FileHunkDiff(
                path=new_p,
                old_path=old_p if old_p != new_p else None,
                status="M",
            )
            continue

        if current_file is None:
            continue

        if line.startswith("new file mode"):
            current_file.status = "A"
            continue
        elif line.startswith("deleted file mode"):
            current_file.status = "D"
            continue
        elif line.startswith("similarity index"):
            current_file.status = "R"
            continue

        hunk_match = RE_HUNK_HEADER.match(line)
        if hunk_match:
            flush_hunk()
            old_start = int(hunk_match.group(1))
            old_lines = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            new_start = int(hunk_match.group(3))
            new_lines = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1
            header_rest = hunk_match.group(5).strip()

            current_hunk = HunkRange(
                old_start=old_start,
                old_lines=old_lines,
                new_start=new_start,
                new_lines=new_lines,
                hunk_header=header_rest,
            )
            continue

        if current_hunk is not None and (line.startswith("+") or line.startswith("-")):
            current_hunk_lines.append(line)

    flush_file()
    return result


def compute_normalized_hunk_hash(hunk_lines: List[str]) -> str:
    """
    Computes a deterministic SHA256 digest of hunk lines with whitespace normalized.
    Allows detecting duplicate changes that differ only in formatting/whitespace.
    """
    norm_lines = []
    for line in hunk_lines:
        prefix = line[0] if line else ""
        content = line[1:].strip() if len(line) > 1 else ""
        if content:
            norm_lines.append(f"{prefix}{content}")
    payload = "\n".join(norm_lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def ranges_overlap(
    start_a: int,
    lines_a: int,
    start_b: int,
    lines_b: int,
    margin: int = 0,
) -> bool:
    """
    Checks if two 1-based line ranges in the same coordinate space intersect.
    When lines == 0 (insertion point), the effective range is [start, start].
    """
    end_a = start_a + max(0, lines_a - 1)
    end_b = start_b + max(0, lines_b - 1)
    return max(start_a, start_b) <= min(end_a, end_b) + margin


def compare_file_hunks(
    hunks_a: List[HunkRange],
    hunks_b: List[HunkRange],
    margin: int = 0,
) -> Tuple[bool, List[Tuple[HunkRange, HunkRange]]]:
    """
    Compares two lists of hunks for the same file (both relative to common merge-base).
    Returns (is_disjoint, overlapping_pairs).
    """
    if not hunks_a or not hunks_b:
        return True, []

    overlapping: List[Tuple[HunkRange, HunkRange]] = []
    for ha in hunks_a:
        for hb in hunks_b:
            if ranges_overlap(ha.old_start, ha.old_lines, hb.old_start, hb.old_lines, margin=margin):
                overlapping.append((ha, hb))

    is_disjoint = len(overlapping) == 0
    return is_disjoint, overlapping
