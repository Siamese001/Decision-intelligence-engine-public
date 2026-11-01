"""
dual_pipeline/token_optimization/pre_gate_screener.py

Deterministic Pre-Gate Screener (Agent 3).
Performs zero-token heuristic validation before invoking Layer 2 LLM extraction.
Pre-validates attendee rosters, metadata schemas, note structures, and scope bounds
to preemptively abort doomed runs before any LLM tokens or provider costs are spent.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

# Estimated tokens averted per doomed run that is caught early (L2 extraction + reasoning)
DEFAULT_AVERTED_TOKENS_PER_RUN = 25_000

# Regex for allowed date formats: MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD
DATE_PATTERNS = [
    re.compile(r"^(0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])[/-](19|20)\d{2}$"),
    re.compile(r"^(19|20)\d{2}[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])$"),
]


class PreGateValidationError(ValueError):
    """Raised when pre-gate validation encounters fatal input defects."""
    pass


@dataclass
class PreGateResult:
    """Outcome of deterministic pre-gate screening."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    tokens_saved_estimate: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_attendees(raw_attendees: Any) -> List[str]:
    """Normalize attendees from string, list, or dict into cleaned names."""
    if not raw_attendees:
        return []
    if isinstance(raw_attendees, list):
        return [str(a).strip() for a in raw_attendees if str(a).strip()]
    if isinstance(raw_attendees, str):
        # Split on comma, semicolon, or newline
        parts = re.split(r"[,;\n]+", raw_attendees)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(raw_attendees, dict):
        return [str(k).strip() for k in raw_attendees.keys() if str(k).strip()]
    return []


def _is_meeting_context(metadata: Dict[str, Any], modality: Optional[str]) -> bool:
    mod_match = str(modality or "").lower() in ("canonicalmodality.transcript", "transcript", "meeting")
    has_keys = any(k in metadata for k in ("meeting_name", "meeting", "attendees"))
    return mod_match or has_keys


def _validate_single_attendee(name: str) -> Optional[str]:
    if len(name) < 2:
        return f"Invalid attendee name (too short): '{name}'"
    if any(c in name for c in ("\x00", "\r", "\n", "\t")):
        return f"Attendee name contains illegal control characters: '{name}'"
    return None


def validate_attendee_roster(
    metadata: Dict[str, Any],
    modality: Optional[str] = None,
    strict: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """Validate attendee attribution roster."""
    errors: List[str] = []
    warnings: List[str] = []

    if not _is_meeting_context(metadata, modality):
        return True, errors, warnings

    attendees = _parse_attendees(metadata.get("attendees"))
    if not attendees:
        msg = "Missing or empty attendee roster for meeting/transcript ingestion."
        if strict or metadata.get("require_attendees", False):
            errors.append(msg)
        else:
            warnings.append(msg)
        return len(errors) == 0, errors, warnings

    for name in attendees:
        err = _validate_single_attendee(name)
        if err:
            errors.append(err)

    return len(errors) == 0, errors, warnings


def validate_metadata_schema(
    metadata: Dict[str, Any],
    strict: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """Validate metadata field types, required keys, and formatting."""
    errors: List[str] = []
    warnings: List[str] = []

    # 1. Project field
    project = metadata.get("project")
    if project is None or not str(project).strip():
        errors.append("Metadata 'project' field must be a non-empty string.")

    # 2. Date format check if date provided
    date_val = metadata.get("date") or metadata.get("meeting_date")
    if date_val:
        date_str = str(date_val).strip()
        if not any(pat.match(date_str) for pat in DATE_PATTERNS):
            msg = f"Metadata date '{date_str}' does not match supported formats (MM-DD-YYYY or YYYY-MM-DD)."
            if strict:
                errors.append(msg)
            else:
                warnings.append(msg)

    # 3. Disallowed project flags
    if metadata.get("is_blacklisted") or metadata.get("quarantine_drop"):
        errors.append("Target project is marked as blacklisted or dropped from intake.")

    return len(errors) == 0, errors, warnings


def validate_source_content(
    source_text: str,
    strict: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """Validate raw text source integrity and minimum bounds."""
    errors: List[str] = []
    warnings: List[str] = []

    if source_text is None:
        errors.append("Source text is None.")
        return False, errors, warnings

    cleaned = source_text.strip()
    if not cleaned:
        errors.append("Source text is empty or whitespace-only.")
        return False, errors, warnings

    if len(cleaned) < 10:
        errors.append(f"Source text is too short ({len(cleaned)} chars; min 10 chars required).")

    if "\x00" in source_text:
        errors.append("Source text contains null bytes (binary corruption detected).")

    return len(errors) == 0, errors, warnings


def validate_scope_bounds(
    metadata: Dict[str, Any],
    source_text: str,
    max_char_limit: int = 1_000_000,
) -> Tuple[bool, List[str], List[str]]:
    """Validate commercial scope bounds and input length ceilings."""
    errors: List[str] = []
    warnings: List[str] = []

    if metadata.get("commercial_boundary_excluded") or metadata.get("out_of_scope"):
        errors.append("Input is explicitly marked as out-of-scope or commercially excluded.")

    if len(source_text) > max_char_limit:
        errors.append(
            f"Input text exceeds maximum safe character ceiling ({len(source_text)} > {max_char_limit})."
        )

    return len(errors) == 0, errors, warnings


def screen_pre_gate(
    metadata: Dict[str, Any],
    source_text: str,
    modality: Optional[str] = None,
    strict: bool = False,
) -> PreGateResult:
    """Run all deterministic pre-gate screening checks."""
    t0 = time.perf_counter()
    all_errors: List[str] = []
    all_warnings: List[str] = []
    checks_run: List[str] = []

    # Check 1: Source Content
    checks_run.append("source_content")
    v_src, err_src, warn_src = validate_source_content(source_text, strict=strict)
    all_errors.extend(err_src)
    all_warnings.extend(warn_src)

    # Check 2: Metadata Schema
    checks_run.append("metadata_schema")
    v_meta, err_meta, warn_meta = validate_metadata_schema(metadata, strict=strict)
    all_errors.extend(err_meta)
    all_warnings.extend(warn_meta)

    # Check 3: Attendee Roster
    checks_run.append("attendee_roster")
    v_att, err_att, warn_att = validate_attendee_roster(metadata, modality=modality, strict=strict)
    all_errors.extend(err_att)
    all_warnings.extend(warn_att)

    # Check 4: Scope Bounds
    checks_run.append("scope_bounds")
    v_scope, err_scope, warn_scope = validate_scope_bounds(metadata, source_text)
    all_errors.extend(err_scope)
    all_warnings.extend(warn_scope)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    is_valid = len(all_errors) == 0
    tokens_saved = DEFAULT_AVERTED_TOKENS_PER_RUN if not is_valid else 0

    return PreGateResult(
        is_valid=is_valid,
        errors=all_errors,
        warnings=all_warnings,
        checks_run=checks_run,
        elapsed_ms=elapsed_ms,
        tokens_saved_estimate=tokens_saved,
    )


def validate_or_raise(
    metadata: Dict[str, Any],
    source_text: str,
    modality: Optional[str] = None,
    strict: bool = False,
) -> PreGateResult:
    """Screen pre-gate and raise PreGateValidationError if invalid."""
    result = screen_pre_gate(metadata, source_text, modality=modality, strict=strict)
    if not result.is_valid:
        raise PreGateValidationError(f"Pre-gate validation failed: {'; '.join(result.errors)}")
    return result
