#!/usr/bin/env python3
"""dual_pipeline/hitl_governance_core/question_validator.py

Strict validator enforcing Wave 5 Question Payload Contracts:
- Mandatory calibrated confidence levels (score, percentage, tier).
- Zero-jargon plain layman language (no file paths, code keywords, identifiers, or flags).
- Full visibility without text truncation (strict display limits; bans slicing and ellipses).
- Atomic presentation (exactly one question, minimum two mutually exclusive options, no bundles).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.hitl_governance_core.findings_table_validator import (
    MAX_FINDINGS_TABLE_LENGTH,
    split_findings_table_context,
    verify_audit_findings_table,
)
from dual_pipeline.hitl_governance_core.layman_clarity import (
    scan_for_jargon,
)

# Display limits established in Wave 1 and locked by Decision D5
MAX_QUESTION_LENGTH: int = 300
MAX_OPTION_LENGTH: int = 250
MIN_OPTIONS: int = 2

# Truncation marker pattern (banned under anti-truncation invariant)
TRUNCATION_PATTERN = re.compile(
    r"(?:\.\.\.|\u2026|\[truncated\]|\[\.\.\.\]|\(truncated\))",
    re.IGNORECASE,
)

# CLI flags pattern (e.g. --strict, --force, -v)
CLI_FLAG_PATTERN = re.compile(
    r"(?:^|\s)-{1,2}[a-zA-Z0-9_\-]+(?:\s|=|$)",
)

# Confidence extraction patterns
PERCENTAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
TIER_PATTERN = re.compile(r"\b(HIGH|MEDIUM|LOW)\b", re.IGNORECASE)


class QuestionPayloadValidationError(ValueError):
    """Raised when a draft question payload violates the Question Contract."""


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a draft question payload."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    rejection_reason: Optional[str] = None

    def raise_for_status(self) -> None:
        """Raise QuestionPayloadValidationError if validation failed."""
        if not self.is_valid:
            msg = self.rejection_reason or "; ".join(self.errors)
            raise QuestionPayloadValidationError(f"Question payload rejected: {msg}")


def check_anti_truncation(text: str, field_name: str) -> List[str]:
    """Verify that text does not contain any truncation artifacts or ellipses."""
    errors: List[str] = []
    if not text or not isinstance(text, str):
        return errors

    if TRUNCATION_PATTERN.search(text):
        errors.append(
            f"ANTI_TRUNCATION_VIOLATION in '{field_name}': Contains ellipses or truncation markers. "
            "All text must be fully visible and unabbreviated."
        )
    return errors


def check_bundled_questions(question_text: str) -> List[str]:
    """Verify that the question text contains exactly one question and is not a bundle."""
    errors: List[str] = []
    if not question_text or not isinstance(question_text, str):
        return ["ATOMIC_QUESTION_VIOLATION: Question text is missing or not a string."]

    # Count question marks
    qmark_count = question_text.count("?")
    if qmark_count > 1:
        errors.append(
            f"ATOMIC_QUESTION_VIOLATION: Question contains {qmark_count} question marks. "
            "Questions must be presented strictly one at a time without bundling."
        )

    # Check for conjunctive compound question phrasing (e.g. "Also, which...", "Additionally, what...")
    compound_pattern = re.compile(
        r"(?:(?:and\s+also|additionally|furthermore|secondly|in\s+addition)[,\s]+(?:what|which|how|should|do|is|are|can))\b",
        re.IGNORECASE,
    )
    if compound_pattern.search(question_text):
        errors.append(
            "ATOMIC_QUESTION_VIOLATION: Detected compound/bundled question clauses. "
            "Decisions must be presented strictly one at a time."
        )

    return errors


# Governed worktree repair patterns and anti-patterns
BANNED_DESTRUCTIVE_LOCK_PATTERNS = [
    re.compile(r"\brm\b.*?\b(?:\.lock|index\.lock)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf?\b", re.IGNORECASE),
    re.compile(r"\bgit\b.*?\breset\b", re.IGNORECASE),
]
LOCK_REMEDIATION_CONTEXT_PATTERNS = [
    re.compile(r"\b(?:stale\s+lock|index\.lock|abandoned\s+lock|lock\s+file|worktree\s+lock|reclaim\s+lock)\b", re.IGNORECASE),
]
GOVERNED_REPAIR_CLI_PATTERN = re.compile(
    r"(?:(?:\.venv/bin/)?python\s+)?(?:tools/)?pr_governance\.py\s+repair-index(?:\s+[a-zA-Z0-9_\-\.]+)*",
    re.IGNORECASE,
)


def check_destructive_lock_remediation(text: str, idx: int) -> List[str]:
    """Verify that text does not propose destructive shell commands for lock recovery."""
    errors: List[str] = []
    if not text or not isinstance(text, str):
        return errors
    target = f"option {idx}" if idx > 0 else "question prompt"
    for pat in BANNED_DESTRUCTIVE_LOCK_PATTERNS:
        if pat.search(text):
            errors.append(
                f"DESTRUCTIVE_LOCK_COMMAND_VIOLATION in {target}: Proposing raw shell lock deletion "
                "or 'git reset' is strictly prohibited. Mandate governed CLI: "
                "'.venv/bin/python tools/pr_governance.py repair-index'."
            )
            break
    return errors


def check_layman_language(text: str, field_name: str) -> List[str]:
    """Enforce the Zero-Jargon Plain English Standard on a string."""
    errors: List[str] = []
    if not text or not isinstance(text, str):
        return errors

    # Whitelist approved governed repair CLI command before scanning for jargon
    sanitized = GOVERNED_REPAIR_CLI_PATTERN.sub("governed repair utility", text)
    sanitized = re.sub(r"\.venv/bin/python\s+", "", sanitized)

    # Check for CLI flags
    flag_matches = CLI_FLAG_PATTERN.findall(sanitized)
    for fm in flag_matches:
        flag_clean = fm.strip()
        if flag_clean and not flag_clean.isdigit():
            errors.append(
                f"ZERO_JARGON_VIOLATION in '{field_name}': Forbidden CLI flag detected: '{flag_clean}'"
            )

    # Base scan for jargon (paths, extensions, code keywords, snake_case, etc.)
    jargon_items = scan_for_jargon(sanitized)
    for j in jargon_items:
        errors.append(f"ZERO_JARGON_VIOLATION in '{field_name}': {j}")

    return errors


# Banned anti-pattern choices that violate knowledge graph provenance and truth invariants
BANNED_ANTI_PATTERN_PATTERNS = [
    re.compile(r"\b(?:ingest\s+as\s+undiarized|undiarized\s+session|undiarized\s+transcript)\b", re.IGNORECASE),
    re.compile(r"\b(?:without\s+(?:individual\s+)?speaker\s+attribution|strip\s+(?:individual\s+)?speaker\s+attribution|drop\s+(?:individual\s+)?speaker\s+attribution)\b", re.IGNORECASE),
    re.compile(r"\b(?:bypass\s+diarization|ignore\s+diarization|discard\s+diarization)\b", re.IGNORECASE),
]


def check_anti_pattern_options(text: str, idx: int) -> List[str]:
    """Verify that an option does not suggest uncalibrated anti-patterns like undiarized ingestion."""
    errors: List[str] = []
    if not text or not isinstance(text, str):
        return errors
    for pat in BANNED_ANTI_PATTERN_PATTERNS:
        if pat.search(text):
            errors.append(
                f"ANTI_PATTERN_VIOLATION in option {idx}: Proposing undiarized ingestion or "
                "discarding speaker attribution is a prohibited anti-pattern that violates "
                "knowledge graph provenance and truth invariants."
            )
            break
    return errors


def validate_option_confidence(
    opt: Any,
    idx: int,
) -> Tuple[List[str], Optional[float], Optional[str]]:
    """Validate that an option carries an explicit, calibrated confidence score, percentage, and tier.

    Returns:
        (errors, extracted_pct, extracted_tier)
    """
    errors: List[str] = []
    text_to_scan = ""

    if isinstance(opt, Mapping):
        # Structured option
        score = opt.get("confidence_score")
        pct = opt.get("confidence_pct")
        tier = opt.get("confidence_level") or opt.get("tier")
        display_text = opt.get("display_text") or opt.get("label") or ""
        text_to_scan = str(display_text)

        if score is None and pct is None:
            errors.append(
                f"MISSING_CONFIDENCE in option {idx}: Neither confidence_score nor confidence_pct provided."
            )
        elif score is not None and not (0.0 <= float(score) <= 1.0):
            errors.append(
                f"INVALID_CONFIDENCE_SCORE in option {idx}: Score {score} must be within [0.0, 1.0]."
            )
        elif pct is not None and not (0.0 <= float(pct) <= 100.0):
            errors.append(
                f"INVALID_CONFIDENCE_PCT in option {idx}: Percentage {pct} must be within [0.0, 100.0]."
            )

        if not tier or str(tier).upper() not in {"HIGH", "MEDIUM", "LOW"}:
            errors.append(
                f"INVALID_CONFIDENCE_TIER in option {idx}: Tier '{tier}' must be HIGH, MEDIUM, or LOW."
            )

    elif isinstance(opt, str):
        text_to_scan = opt
    else:
        errors.append(
            f"INVALID_OPTION_TYPE in option {idx}: Expected string or dictionary, got {type(opt).__name__}."
        )
        return errors, None, None

    # Check confidence presence in visible text
    pct_match = PERCENTAGE_PATTERN.search(text_to_scan)
    tier_match = TIER_PATTERN.search(text_to_scan)

    if not pct_match:
        errors.append(
            f"MISSING_CONFIDENCE_IN_TEXT in option {idx}: Display text does not declare a percentage (e.g. '65%')."
        )
    if not tier_match:
        errors.append(
            f"MISSING_TIER_IN_TEXT in option {idx}: Display text does not declare a tier (HIGH, MEDIUM, or LOW)."
        )

    extracted_pct = float(pct_match.group(1)) if pct_match else None
    extracted_tier = tier_match.group(1).upper() if tier_match else None

    # Parity check between structured values and text representation
    if isinstance(opt, Mapping) and extracted_pct is not None:
        struct_pct = opt.get("confidence_pct")
        if struct_pct is None and opt.get("confidence_score") is not None:
            struct_pct = round(float(opt["confidence_score"]) * 100.0, 2)
        if struct_pct is not None and abs(extracted_pct - float(struct_pct)) > 1.0:
            errors.append(
                f"CONFIDENCE_PARITY_MISMATCH in option {idx}: Text percentage ({extracted_pct}%) "
                f"does not match structured percentage ({struct_pct}%)."
            )

    if isinstance(opt, Mapping) and extracted_tier is not None:
        struct_tier = str(opt.get("confidence_level") or opt.get("tier") or "").upper()
        if struct_tier and extracted_tier != struct_tier:
            errors.append(
                f"TIER_PARITY_MISMATCH in option {idx}: Text tier '{extracted_tier}' "
                f"does not match structured tier '{struct_tier}'."
            )

    return errors, extracted_pct, extracted_tier


def split_speaker_review_context(question_text: str) -> Tuple[Optional[str], str]:
    """Exempt verbatim transcript evidence blocks from question display budgets."""
    if "TARGET" not in question_text or "UNRESOLVED" not in question_text:
        return None, question_text

    evidence_lines: List[str] = []
    prompt_lines: List[str] = []
    in_evidence = False

    for line in question_text.splitlines():
        stripped = line.strip()
        if not stripped:
            (evidence_lines if in_evidence else prompt_lines).append(line)
            continue
        is_evidence = (
            stripped.startswith(
                ("Meeting/source:", "Review one target", "Transcript start:", "Transcript end:", "--- Separate")
            )
            or re.match(r"^(?:[-*+]\s+)?(?:[`*🎯\s]*)(?:TARGET|BEFORE \d+|AFTER \d+)\b", stripped) is not None
        )
        if is_evidence:
            in_evidence = True
            evidence_lines.append(line)
        elif in_evidence and ("who is speaking" in stripped.lower() or stripped.endswith("?")):
            in_evidence = False
            prompt_lines.append(line)
        elif in_evidence:
            evidence_lines.append(line)
        else:
            prompt_lines.append(line)

    ev_text = "\n".join(evidence_lines).strip()
    prompt = "\n".join(prompt_lines).strip()

    # Deduplicate repeated identical per-turn questions in grouped review contexts
    clean_prompts = [p.strip() for p in prompt_lines if p.strip()]
    if clean_prompts and len(set(clean_prompts)) == 1 and clean_prompts[0].lower().startswith("who is speaking"):
        prompt = clean_prompts[0]

    if ev_text and "TARGET" in ev_text and "UNRESOLVED" in ev_text:
        return ev_text, (prompt or "Who is speaking in the TARGET turn?")
    return None, question_text


def validate_question_payload(
    question: str,
    options: Sequence[Any],
    metadata: Optional[Mapping[str, Any]] = None,
) -> ValidationResult:
    """Fully validate a draft question and options payload against Wave 5 contracts.

    Checks:
    1. Budget / display limits: question <= 300 chars, each option <= 250 chars.
    2. Anti-truncation: zero ellipses or slicing indicators.
    3. Atomic question: exactly one question, no bundling.
    4. Options: at least 2 mutually exclusive choices; recommended option first and labeled.
    5. Calibrated confidence: percentage and tier required and consistent on every option.
    6. Layman language: zero file paths, code identifiers, flags, or implementation jargon.

    Returns:
        ValidationResult with itemized errors and rejection reason if invalid.
    """
    errors: List[str] = []

    # 1. Question string validation
    if not isinstance(question, str) or not question.strip():
        errors.append("EMPTY_QUESTION: Question text cannot be empty.")
        return ValidationResult(is_valid=False, errors=errors, rejection_reason="Empty question")

    question_clean = question.strip()
    errors.extend(verify_audit_findings_table(question_clean))
    evidence_block, prompt_text = split_speaker_review_context(question_clean)
    if evidence_block:
        active_prompt = prompt_text
    else:
        table_block, table_prompt = split_findings_table_context(question_clean)
        active_prompt = table_prompt if table_block else question_clean

    # Budget limit (verbatim transcript evidence and dimension findings tables are exempt)
    if len(active_prompt) > MAX_QUESTION_LENGTH:
        errors.append(
            f"QUESTION_LENGTH_EXCEEDED: Question prompt is {len(active_prompt)} characters "
            f"(maximum budget is {MAX_QUESTION_LENGTH} characters; findings table budget is {MAX_FINDINGS_TABLE_LENGTH})."
        )

    # Anti-truncation
    errors.extend(check_anti_truncation(question_clean, "question"))

    # Atomic presentation & no bundling (evaluated on active prompt)
    errors.extend(check_bundled_questions(active_prompt))

    # Layman clarity on question prompt
    errors.extend(check_layman_language(active_prompt, "question"))

    # Destructive lock command check on question prompt
    errors.extend(check_destructive_lock_remediation(question_clean, 0))

    # 2. Options validation
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        errors.append("INVALID_OPTIONS_CONTAINER: Options must be a sequence of options.")
        return ValidationResult(
            is_valid=False, errors=errors, rejection_reason="Invalid options container"
        )

    if len(options) < MIN_OPTIONS:
        errors.append(
            f"INSUFFICIENT_OPTIONS: Provided {len(options)} options; at least {MIN_OPTIONS} are required."
        )

    is_lock_context = any(pat.search(question_clean) for pat in LOCK_REMEDIATION_CONTEXT_PATTERNS)
    has_governed_repair = False
    has_recommended = False
    first_is_recommended = False

    for idx, opt in enumerate(options, start=1):
        # Extract display text for length and content checks
        if isinstance(opt, Mapping):
            disp = opt.get("display_text") or opt.get("label") or ""
            is_rec = bool(opt.get("recommended")) or "(recommended)" in str(disp).lower()
        elif isinstance(opt, str):
            disp = opt
            is_rec = "(recommended)" in opt.lower()
        else:
            errors.append(
                f"INVALID_OPTION_TYPE in option {idx}: Expected dict or str, got {type(opt).__name__}."
            )
            continue

        disp_clean = str(disp).strip()
        if not disp_clean:
            errors.append(f"EMPTY_OPTION in option {idx}: Option text cannot be empty.")
            continue

        # Budget limit on option
        if len(disp_clean) > MAX_OPTION_LENGTH:
            errors.append(
                f"OPTION_LENGTH_EXCEEDED in option {idx}: Option is {len(disp_clean)} characters "
                f"(maximum budget is {MAX_OPTION_LENGTH} characters)."
            )

        # Destructive lock command checks (Critical Safety Invariant)
        errors.extend(check_destructive_lock_remediation(disp_clean, idx))
        if GOVERNED_REPAIR_CLI_PATTERN.search(disp_clean):
            has_governed_repair = True

        # Anti-pattern checks
        errors.extend(check_anti_pattern_options(disp_clean, idx))

        # Anti-truncation
        errors.extend(check_anti_truncation(disp_clean, f"option_{idx}"))

        # Layman language on option
        errors.extend(check_layman_language(disp_clean, f"option_{idx}"))

        # Confidence checks
        conf_errs, _, _ = validate_option_confidence(opt, idx)
        errors.extend(conf_errs)

        # Track recommended status
        if is_rec:
            has_recommended = True
            if idx == 1:
                first_is_recommended = True

    # Mandate governed repair tool for lock remediation decisions
    if is_lock_context and not has_governed_repair:
        errors.append(
            "MANDATORY_GOVERNED_REPAIR_VIOLATION: Lock remediation decisions must mandate "
            "governed CLI reference: '.venv/bin/python tools/pr_governance.py repair-index'."
        )

    # Recommended option ordering rule
    if has_recommended and not first_is_recommended:
        errors.append(
            "RECOMMENDED_ORDERING_VIOLATION: The recommended option must be listed first "
            "and labeled with '(Recommended)'."
        )

    if errors:
        rejection_reason = f"Question payload rejected due to {len(errors)} violation(s): {'; '.join(errors)}"
        return ValidationResult(is_valid=False, errors=errors, rejection_reason=rejection_reason)

    return ValidationResult(is_valid=True, errors=[])


def assert_question_payload_valid(
    question: str,
    options: Sequence[Any],
    metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    """Validate question payload and raise QuestionPayloadValidationError on any violation."""
    res = validate_question_payload(question=question, options=options, metadata=metadata)
    res.raise_for_status()
