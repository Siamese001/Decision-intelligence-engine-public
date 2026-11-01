"""Strict length and execution-evidence checks for newly authored wave plans."""

from __future__ import annotations

import re
from typing import Sequence

from dual_pipeline.parameters import compute_scaled_plan_word_limit, get_planning_word_limits
_plan_limits = get_planning_word_limits()
MAX_PLAN_WORDS = _plan_limits["max_plan_words"]
MAX_SCALED_PLAN_WORDS = _plan_limits["max_scaled_plan_words"]
BASE_PLAN_WORDS = _plan_limits["base_plan_words"]
WORDS_PER_WAVE = _plan_limits["words_per_wave"]
WORDS_PER_DELIVERABLE = _plan_limits["words_per_deliverable"]

_GATE_HEADING = re.compile(
    r"^#{3,4}\s+.*(?:receipt|completion\s+gate|exit\s+gate|verification).*",
    re.IGNORECASE,
)
_FIELD_PATTERNS = {
    "Run": re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?Run(?:\*\*)?\s*:", re.IGNORECASE),
    "Input": re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?Input(?:\*\*)?\s*:", re.IGNORECASE),
    "Expected": re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?Expected(?:\*\*)?\s*:", re.IGNORECASE),
    "Verify independently": re.compile(
        r"^\s*(?:[-*+]\s*)?(?:\*\*)?(?:Verify independently|Independent check)(?:\*\*)?\s*:",
        re.IGNORECASE,
    ),
    "Fail if": re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?Fail if(?:\*\*)?\s*:", re.IGNORECASE),
    "Record": re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?Record(?:\*\*)?\s*:", re.IGNORECASE),
}
_HITL_FENCE_RE = re.compile(
    r"```json:(?:deferred-decisions|decisions|hitl)\b.*?```",
    re.IGNORECASE | re.DOTALL,
)
_HITL_TABLE_HEADER_RE = re.compile(
    r"^\s*\|.*?\b(?:Decision|Candidate\s+ID)\b.*?\|.*?\b(?:Options|Confidence|Handling|\u0394|Delta)\b.*?\|",
    re.IGNORECASE,
)


def strip_hitl_decision_blocks(content: str) -> str:
    """Strip embedded HITL decision JSON blocks and decision tables before word counting."""
    stripped = _HITL_FENCE_RE.sub("", content)
    out_lines: list[str] = []
    in_decision_table = False
    for line in stripped.splitlines():
        if not in_decision_table and _HITL_TABLE_HEADER_RE.match(line):
            in_decision_table = True
            continue
        if in_decision_table:
            if line.strip().startswith("|"):
                continue
            in_decision_table = False
        out_lines.append(line)
    return "\n".join(out_lines)


def compute_max_plan_words(waves: Sequence[object]) -> int:
    """Compute scope-scaled word ceiling: min(2500, max(1000, 400 + 250*N_waves + 25*M_items))."""
    num_waves = len(waves)
    total_items = sum(len(getattr(w, "deliverable_items", []) or []) for w in waves)
    if compute_scaled_plan_word_limit is not None:
        try:
            return compute_scaled_plan_word_limit(num_waves, total_items)
        except Exception:
            pass
    scaled = BASE_PLAN_WORDS + (WORDS_PER_WAVE * num_waves) + (WORDS_PER_DELIVERABLE * total_items)
    return min(MAX_SCALED_PLAN_WORDS, max(MAX_PLAN_WORDS, scaled))


def _gate_lines(wave: object) -> list[str]:
    lines = getattr(wave, "lines", [])
    active = False
    result: list[str] = []
    for _line_number, line in lines:
        if _GATE_HEADING.search(line.strip()):
            active = True
            continue
        if active and re.match(r"^\s*#{3,4}\s+", line):
            break
        if active:
            result.append(line)
    return result


def validate_execution_evidence(content: str, waves: Sequence[object], filename: str) -> list[str]:
    """Require bounded plans and explicit run/evidence fields in each wave gate."""
    errors: list[str] = []
    countable_content = strip_hitl_decision_blocks(content)
    word_count = len(countable_content.split())
    allowed_words = compute_max_plan_words(waves)
    if word_count > allowed_words:
        errors.append(
            f"{filename}: Plan has {word_count} countable words; wave plans of this scope "
            f"({len(waves)} wave(s)) are capped at {allowed_words} words "
            f"(base {MAX_PLAN_WORDS} floor, max {MAX_SCALED_PLAN_WORDS} ceiling)."
        )

    for wave in waves:
        gate_lines = _gate_lines(wave)
        if not gate_lines:
            continue  # The structural validator reports the missing gate heading.
        present = {
            label for label, pattern in _FIELD_PATTERNS.items()
            if any(pattern.search(line) for line in gate_lines)
        }
        missing = [label for label in _FIELD_PATTERNS if label not in present]
        if missing:
            number = getattr(wave, "number", "?")
            errors.append(
                f"{filename}: Wave {number} completion gate must specify: {', '.join(missing)}. "
                "Receipts document runs; independently observed behavior determines completion."
            )
    return errors

