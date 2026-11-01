"""Counting and forbidden-pattern auditing for engineering outputs."""

from __future__ import annotations

import re
from typing import List

from .models import ValidationResult

# Thresholds governed by policy_registry.yaml and engineering-output.md
from dual_pipeline.parameters import get_engineering_output_limits
_limits = get_engineering_output_limits()
MAX_ENGINEERING_WORDS = _limits["max_words"]
TARGET_WORDS = _limits["target_words"]
TARGET_MIN_WORDS = _limits["target_min_words"]
TARGET_MAX_WORDS = _limits["target_max_words"]
MAX_ENGINEERING_CHARS = _limits["max_chars"]
MAX_PROGRESS_WORDS = _limits["progress_max_words"]

# Patterns that represent procedural scratchpad, telemetry, or all-wave inventory
FORBIDDEN_PROCESS_PATTERNS = [
    (r"###?\s*🧠?\s*Agent\s+Reasoning\s+Scratchpad", "Agent Reasoning Scratchpad header"),
    (r"\bActive\s+Model\s+&\s+Provider\b", "Active Model & Provider telemetry"),
    (r"\bInvocation\s+Role\b", "Invocation Role telemetry"),
    (r"\bCognitive\s+Breakdown\s+&\s+Thinking\b", "Cognitive Breakdown section"),
    (r"\bReasoning\s+Steps\s*:", "Reasoning Steps section"),
    (r"\bAPI\s+&\s+Tool\s+Invocations\s*:", "API & Tool Invocations log"),
]

# Table headers indicating an all-wave historical inventory
WAVE_TABLE_HEADER_PATTERNS = [
    r"\|\s*Wave\s*(?:Number)?\s*\|\s*(?:Description|Scope|Completion|Status)",
    r"\|\s*Wave\s*\|\s*Status\s*\|\s*Completion",
]


def normalize_text(text: str) -> str:
    """Normalize line endings (CRLF to LF) and trim surrounding whitespace."""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def count_words(text: str) -> int:
    """Count whitespace-delimited words on normalized text."""
    normalized = normalize_text(text)
    if not normalized:
        return 0
    return len(normalized.split())


def strip_deliverable_urls(text: str) -> str:
    """Strip target URLs of markdown file:// links for character counting.

    Per engineering-output.md Section 1.7 and 1.9, requested primary deliverables
    are exempt from the character completion cap; their target URLs do not penalize
    concise completion responses while preserving clickable absolute paths.
    """
    return re.sub(r"\]\(file:///[^)]+\)", "](file://...)", text)


def count_characters(text: str, exclude_deliverable_urls: bool = False) -> int:
    """Count total characters on normalized text."""
    normalized = normalize_text(text)
    if not normalized:
        return 0
    if exclude_deliverable_urls:
        normalized = strip_deliverable_urls(normalized)
    return len(normalized)


def find_forbidden_process_sections(text: str) -> List[str]:
    """Detect forbidden scratchpad, telemetry, tool lists, and wave status tables."""
    normalized = normalize_text(text)
    violations: List[str] = []

    for pattern, description in FORBIDDEN_PROCESS_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            violations.append(f"Forbidden process section detected: {description}")

    for pattern in WAVE_TABLE_HEADER_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            violations.append(
                "Forbidden procedural inventory: All-wave status table must be omitted "
                "from ordinary engineering completion output."
            )
            break

    return violations


def find_forbidden_jargon_sections(text: str) -> List[str]:
    """Detect forbidden code syntax, snake_case, and implementation jargon."""
    normalized = normalize_text(text)
    if not normalized:
        return []
    # Strip markdown deliverable links completely to avoid false positives on required clickable deliverable links
    scannable = re.sub(
        r"\[`?[^`\]]+`?\]\((?:file://[^)]+|https?://[^)]+)\)",
        "[Deliverable Link]",
        normalized,
    )
    from dual_pipeline.hitl_governance_core.layman_clarity import scan_for_jargon

    violations = scan_for_jargon(scannable)
    return [f"Forbidden technical jargon detected: {v}" for v in violations]


def validate_text_limits(
    text: str,
    max_words: int = MAX_ENGINEERING_WORDS,
    max_chars: int = MAX_ENGINEERING_CHARS,
    exclude_deliverable_urls: bool = True,
) -> List[str]:
    """Validate word and character limits against policy thresholds."""
    errors: List[str] = []
    w_count = count_words(text)
    c_count = count_characters(text, exclude_deliverable_urls=exclude_deliverable_urls)

    if w_count > max_words:
        errors.append(
            f"Word count {w_count} exceeds maximum allowed ceiling of {max_words} words."
        )

    if c_count > max_chars:
        errors.append(
            f"Character count {c_count} exceeds maximum allowed ceiling of {max_chars} characters."
        )

    return errors


def validate_progress_update(text: str) -> ValidationResult:
    """Validate minimal progress update against 20-word ceiling and clean style."""
    w_count = count_words(text)
    c_count = count_characters(text)
    errors: List[str] = []
    warnings: List[str] = []

    if w_count > MAX_PROGRESS_WORDS:
        errors.append(
            f"Progress update contains {w_count} words; exceeds maximum ceiling of "
            f"{MAX_PROGRESS_WORDS} words."
        )

    forbidden = find_forbidden_process_sections(text)
    errors.extend(forbidden)
    jargon = find_forbidden_jargon_sections(text)
    errors.extend(jargon)

    return ValidationResult(
        is_valid=(len(errors) == 0),
        word_count=w_count,
        char_count=c_count,
        errors=errors,
        warnings=warnings,
        details={"type": "progress_update"},
    )
