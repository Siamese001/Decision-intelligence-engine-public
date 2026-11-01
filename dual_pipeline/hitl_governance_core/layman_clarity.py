"""dual_pipeline/hitl_governance_core/layman_clarity.py

Zero-Jargon Invariant, Plain-English Validation, and Operational Impact Governance.
Ensures options and prompts are articulated in clear layman terms, free of internal code syntax.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class LaymanClarityError(ValueError):
    """Raised when text or option payloads violate the Zero-Jargon Layman Standard."""


# Forbidden file extension pattern (e.g. .py, .json, .yaml, .ts, .sqlite, etc.)
FORBIDDEN_FILE_EXTENSIONS_PATTERN = re.compile(
    r"(?:^|[\s\"'(\[{</\\])(?:[a-zA-Z0-9_\-./\\]+[/\\])?[a-zA-Z0-9_\-]+\.(?:py|json|jsonl|sqlite|db|yaml|yml|md|sh|sql|csv|ts|js|jsx|tsx|toml|ini|cfg|env|conf)\b",
    re.IGNORECASE,
)
# Forbidden filesystem paths / prefixes
FORBIDDEN_PATH_PREFIXES_PATTERN = re.compile(
    r"\b(?:tools|contracts|dual_pipeline|pipeline_a|pipeline_b|storage|tests|config|governance|reports)/[a-zA-Z0-9_\-./]+",
    re.IGNORECASE,
)
# Forbidden code keywords and syntax tokens
FORBIDDEN_CODE_KEYWORDS_PATTERN = re.compile(
    r"\b(?:def|class|import|lambda|async|await|return|kwargs|__init__|dict|list|tuple|self|None|null|boolean|enum)\b"
)

# Forbidden AST / interpreter syntax
FORBIDDEN_AST_SYNTAX_PATTERN = re.compile(
    r"\b(?:ast\.[a-zA-Z0-9_]+|os\.environ|sys\.path)\b",
    re.IGNORECASE,
)

# Forbidden exception / error names (e.g. ValueError, HitlConfidenceMissingError)
FORBIDDEN_EXCEPTION_NAMES_PATTERN = re.compile(
    r"\b[A-Za-z0-9]*(?:Error|Exception)\b"
)

# Forbidden git plumbing internals
FORBIDDEN_GIT_INTERNALS_PATTERN = re.compile(
    r"\b(?:HEAD~[0-9]+|cherry-pick|reflog|worktree|submodule|git\s+(?:add|commit|checkout|push|pull|rebase|merge))\b",
    re.IGNORECASE,
)

# Forbidden snake_case identifiers (e.g. margin_threshold, c_top, decision_store)
# Must contain at least one underscore between alphanumeric segments.
FORBIDDEN_SNAKE_CASE_PATTERN = re.compile(
    r"\b[a-zA-Z0-9]+_[a-zA-Z0-9_]+\b"
)

# Forbidden camelCase identifiers (e.g. marginThreshold, cTop)
FORBIDDEN_CAMEL_CASE_PATTERN = re.compile(
    r"\b[a-z]+[A-Z][a-zA-Z0-9]*\b"
)

# Allowed uppercase acronyms and common capitalized terms in executive options
ALLOWED_PASCAL_WHITELIST = frozenset({
    "Option", "Recommended", "Confidence", "Impact", "Operational",
    "High", "Medium", "Low", "Tier", "Bayesian", "Status", "Decision",
    "Action", "Primary", "Secondary", "Alternative", "Standard",
    "Enterprise", "Execution", "Clearance", "Architecture", "Service",
    "Gateway", "Network", "Database", "Security", "Policy", "Compliance",
})

# Forbidden PascalCase code identifiers (e.g. MarginEvaluator, HitlRequirementEnvelope)
# Matches words with 2 or more capitalized segments inside.
FORBIDDEN_PASCAL_CODE_PATTERN = re.compile(
    r"\b(?:Pydantic|(?:[A-Z][a-z0-9]+){2,})\b"
)

# Operational impact keywords required or recognized in substantive impact statements
OPERATIONAL_IMPACT_INDICATORS = frozenset({
    "enables", "prevents", "removes", "preserves", "deploys", "updates",
    "retires", "replaces", "eliminates", "mitigates", "avoids", "allows",
    "restricts", "reduces", "improves", "guarantees", "ensures", "halts",
    "proceeds", "blocks", "terminates", "maintains", "risk", "benefit",
    "trade-off", "tradeoff", "overhead", "latency", "downtime", "safety",
    "outcome", "consequence", "throughput", "reliability", "cost", "failure",
})


# Forbidden Greek letters, math variables, and LaTeX expressions
FORBIDDEN_GREEK_AND_MATH_PATTERN = re.compile(
    r"(?:[\u0370-\u03FF]|\\(?:kappa|delta|alpha|beta|gamma|theta|sigma|omega|mu|lambda|tau)\b|\b(?:kappa)\b|\$[a-zA-Z0-9_\\]+\$)",
    re.IGNORECASE,
)

# Forbidden technical plumbing and database acronyms
FORBIDDEN_PLUMBING_ACRONYMS_PATTERN = re.compile(
    r"\b(?:ACID|WAL|JSONL|AST|ORM|RPC|IPC|POSIX|CRUD|DDL|DML|PRAGMA)\b"
)

# Forbidden inline SQL query fragments in user-facing prose
FORBIDDEN_SQL_QUERY_PATTERN = re.compile(
    r"\bSELECT\s+.+?\s+FROM\s+[a-zA-Z0-9_]+",
    re.IGNORECASE | re.DOTALL,
)


_SIMPLE_JARGON_RULES: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (FORBIDDEN_PATH_PREFIXES_PATTERN, "Forbidden filesystem path detected: '{m}'"),
    (FORBIDDEN_CODE_KEYWORDS_PATTERN, "Forbidden code keyword detected: '{m}'"),
    (FORBIDDEN_AST_SYNTAX_PATTERN, "Forbidden AST/system identifier detected: '{m}'"),
    (FORBIDDEN_EXCEPTION_NAMES_PATTERN, "Forbidden exception/error name detected: '{m}'"),
    (FORBIDDEN_GIT_INTERNALS_PATTERN, "Forbidden git internal token detected: '{m}'"),
    (FORBIDDEN_GREEK_AND_MATH_PATTERN, "Forbidden Greek or mathematical symbol detected: '{m}'"),
    (FORBIDDEN_PLUMBING_ACRONYMS_PATTERN, "Forbidden technical plumbing acronym detected: '{m}'"),
)


def _scan_casing_jargon(sanitized: str) -> List[str]:
    found: List[str] = []
    for sc in set(FORBIDDEN_SNAKE_CASE_PATTERN.findall(sanitized)):
        if any(c.isalpha() for c in sc):
            found.append(f"Forbidden snake_case identifier detected: '{sc}'")
    for cc in set(FORBIDDEN_CAMEL_CASE_PATTERN.findall(sanitized)):
        found.append(f"Forbidden camelCase identifier detected: '{cc}'")
    for pc in set(FORBIDDEN_PASCAL_CODE_PATTERN.findall(sanitized)):
        if pc not in ALLOWED_PASCAL_WHITELIST:
            found.append(f"Forbidden PascalCase code identifier detected: '{pc}'")
    return found


def scan_for_jargon(text: str) -> List[str]:
    """Scans a string for forbidden code tokens, file paths, and implementation jargon."""
    if not text or not isinstance(text, str):
        return []
    violations: List[str] = [
        f"Forbidden file reference detected: '{m.strip(' \t\n\r\"\'([{</\\')}'"
        for m in FORBIDDEN_FILE_EXTENSIONS_PATTERN.findall(text)
    ]
    for pattern, template in _SIMPLE_JARGON_RULES:
        for match in set(pattern.findall(text)):
            violations.append(template.format(m=match))
    for z in set(re.findall(r"\[\s*\d+%?\s*[–\-]\s*\d+%?\s*,\s*n=0\s*\]", text, re.IGNORECASE)):
        violations.append(f"Forbidden meaningless zero-sample interval detected: '{z}'")
    sanitized = re.sub(r"\[\s*\d+%?\s*[–\-]\s*\d+%?\s*(?:,\s*n=\d+)?\s*\]", " ", text)
    violations.extend(_scan_casing_jargon(sanitized))
    for sql_m in set(FORBIDDEN_SQL_QUERY_PATTERN.findall(text)):
        violations.append(f"Forbidden inline SQL query detected: '{' '.join(sql_m.split())[:60]}'")
    return sorted(list(set(violations)))


def contains_jargon(text: str) -> bool:
    """Convenience boolean check for whether text contains any forbidden jargon."""
    return len(scan_for_jargon(text)) > 0


def validate_plain_english(text: str, field_name: str = "text") -> List[str]:
    """
    Validates that a string is non-empty, of sufficient descriptive length,
    and adheres 100% to the Zero-Jargon Invariant.
    """
    errors: List[str] = []
    if not isinstance(text, str):
        return [f"LAYMAN_INVALID_TYPE: '{field_name}' must be a string, got {type(text).__name__}."]

    stripped = text.strip()
    if not stripped:
        return [f"LAYMAN_EMPTY_FIELD: '{field_name}' cannot be empty."]

    if len(stripped) < 5:
        errors.append(f"LAYMAN_TOO_SHORT: '{field_name}' is too brief ({len(stripped)} chars, min 5 required).")

    jargon_violations = scan_for_jargon(stripped)
    for j in jargon_violations:
        errors.append(f"LAYMAN_JARGON_VIOLATION in '{field_name}': {j}")

    return errors


def validate_impact_statement(text: str, field_name: str = "impact_statement") -> List[str]:
    """
    Validates that an operational impact statement describes operational reality
    (benefits, trade-offs, state mutations) in plain English without technical jargon.
    """
    errors: List[str] = []
    if not isinstance(text, str):
        return [f"LAYMAN_INVALID_TYPE: '{field_name}' must be a string, got {type(text).__name__}."]

    stripped = text.strip()
    if not stripped:
        return [f"LAYMAN_MISSING_IMPACT: '{field_name}' is mandatory and cannot be empty."]

    # Strip leading "Impact: " or "Operational Impact: "
    body = re.sub(r"^(?:operational\s+)?impact:\s*", "", stripped, flags=re.IGNORECASE).strip()
    if len(body) < 15:
        errors.append(
            f"LAYMAN_IMPACT_TOO_BRIEF: '{field_name}' must be substantive (min 15 chars beyond prefix, got {len(body)})."
        )

    # Check for jargon
    jargon_violations = scan_for_jargon(stripped)
    for j in jargon_violations:
        errors.append(f"LAYMAN_JARGON_VIOLATION in '{field_name}': {j}")

    # Check for operational impact indicators in the body
    words = {w.lower().strip(".,;:\"'()[]{}") for w in body.split()}
    has_indicator = bool(words & OPERATIONAL_IMPACT_INDICATORS)
    if not has_indicator:
        errors.append(
            f"LAYMAN_IMPACT_LACKS_OPERATIONAL_SUBSTANCE: '{field_name}' must articulate operational outcomes, risks, or benefits."
        )

    return errors


def _validate_option_text_fields(opt: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    for key, err_code in (
        ("display_text", "LAYMAN_MISSING_DISPLAY_TEXT"),
        ("label", "LAYMAN_MISSING_LABEL"),
        ("plain_english_summary", "LAYMAN_MISSING_SUMMARY"),
    ):
        val = opt.get(key)
        if not val or not isinstance(val, str) or not val.strip():
            errors.append(f"{err_code}: Option must provide a non-empty string '{key}'.")
        else:
            errors.extend(validate_plain_english(val, field_name=key))
    impact = opt.get("impact_statement")
    if not impact or not isinstance(impact, str):
        errors.append("LAYMAN_MISSING_IMPACT: Option must provide an 'impact_statement'.")
    else:
        errors.extend(validate_impact_statement(impact, field_name="impact_statement"))
    return errors


def _validate_option_confidence_fields(opt: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    score = opt.get("confidence_score")
    if score is None or not isinstance(score, (int, float)) or not (0.0 <= float(score) <= 1.0):
        errors.append(f"LAYMAN_INVALID_CONFIDENCE_SCORE: Expected float in [0.0, 1.0], got {score!r}.")
    pct = opt.get("confidence_pct")
    if pct is None or not isinstance(pct, (int, float)) or not (0.0 <= float(pct) <= 100.0):
        errors.append(f"LAYMAN_INVALID_CONFIDENCE_PCT: Expected number in [0.0, 100.0], got {pct!r}.")
    tier = opt.get("confidence_level") or opt.get("tier")
    if tier not in {"HIGH", "MEDIUM", "LOW"}:
        errors.append(f"LAYMAN_INVALID_TIER: Expected HIGH, MEDIUM, or LOW, got {tier!r}.")
    ci = opt.get("credible_interval")
    if ci is None or not isinstance(ci, (list, tuple)) or len(ci) != 2:
        errors.append(f"LAYMAN_INVALID_CREDIBLE_INTERVAL: Expected 2-element sequence [lower, upper], got {ci!r}.")
    else:
        try:
            lo, hi = float(ci[0]), float(ci[1])
            if not (0.0 <= lo <= hi <= 1.0):
                errors.append(f"LAYMAN_INVALID_INTERVAL_RANGE: Expected 0.0 <= lo <= hi <= 1.0, got [{lo}, {hi}].")
        except (ValueError, TypeError):
            errors.append(f"LAYMAN_INVALID_INTERVAL_TYPES: Non-numeric values in credible interval: {ci!r}.")
    return errors


def validate_layman_option(opt: Mapping[str, Any]) -> List[str]:
    """Validates that an option dictionary adheres to hitl-option.schema.json and Zero-Jargon standard."""
    if not isinstance(opt, Mapping):
        return ["LAYMAN_INVALID_OPTION: Option must be a dictionary or mapping."]
    return _validate_option_text_fields(opt) + _validate_option_confidence_fields(opt)


def should_render_credible_interval(
    n: Optional[int],
    lo: Optional[float] = None,
    hi: Optional[float] = None,
    uncertainty_display: Optional[str] = None,
) -> bool:
    """Determines whether a Bayesian credible interval is meaningful and authorized for display.

    Suppresses intervals when:
      - uncertainty_display is 'never'
      - sample count n is 0 or None (uncalibrated prior)
      - interval width (hi - lo) >= 0.80 (uninformative / degenerate dispersion)
      - uncertainty_display is 'sparse_only' and n < 5 (insufficient empirical mass)
    """
    from dual_pipeline.parameters import get_hitl_uncertainty_display

    mode = uncertainty_display or get_hitl_uncertainty_display()
    if mode == "never":
        return False
    if n is None or n <= 0:
        return False
    if lo is not None and hi is not None:
        try:
            lo_f, hi_f = float(lo), float(hi)
            if lo_f >= hi_f:
                return False
            width = hi_f - lo_f
            if width >= 0.80:
                return False
        except (ValueError, TypeError):
            return False
    if mode == "sparse_only":
        return n >= 5
    return True


def format_layman_display_label(opt: Mapping[str, Any]) -> str:
    """Formats the user-facing option string for interactive ask_user_question presentation."""
    rec_prefix = "(Recommended) " if opt.get("recommended") else ""
    label = opt.get("label", "Option")
    conf_pct = int(round(float(opt.get("confidence_pct", 0.0))))
    tier = str(opt.get("confidence_level") or opt.get("tier") or "MEDIUM")

    ci = opt.get("credible_interval")
    ci_str = ""
    if ci and isinstance(ci, (list, tuple)) and len(ci) >= 2:
        lo, hi = float(ci[0]), float(ci[1])
        n = opt.get("sample_size") or opt.get("confidence_sample_count")
        if should_render_credible_interval(n, lo, hi):
            lo_pct = int(round(lo * 100))
            hi_pct = int(round(hi * 100))
            n_str = f", n={n}" if n is not None else ""
            ci_str = f" [{lo_pct}%–{hi_pct}%{n_str}]"

    summary = opt.get("plain_english_summary", "")
    summary_part = f" - {summary}" if summary and summary != label else ""
    impact = opt.get("impact_statement", "")
    impact_part = f" Impact: {impact}" if impact else ""

    return f"{rec_prefix}{label} (Confidence: {tier} ({conf_pct}%){ci_str}){summary_part}.{impact_part}".strip()


def build_layman_option(
    label: str,
    plain_english_summary: str,
    impact_statement: str,
    confidence_score: float,
    credible_interval: Optional[Tuple[float, float]] = None,
    sample_size: Optional[int] = None,
    evidence_refs: Optional[Sequence[str]] = None,
    recommended: bool = False,
    prior_adjustment: float = 0.0,
    display_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Factory creating a validated layman option compliant with hitl-option.schema.json v1.2.0."""
    score = max(0.0, min(1.0, float(confidence_score)))
    tier = "HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW")
    ci = list(credible_interval) if credible_interval else [max(0.0, score - 0.05), min(1.0, score + 0.05)]

    opt: Dict[str, Any] = {
        "label": label.strip(),
        "plain_english_summary": plain_english_summary.strip(),
        "impact_statement": impact_statement.strip(),
        "confidence_score": round(score, 4),
        "confidence_pct": round(score * 100.0, 2),
        "confidence_level": tier,
        "tier": tier,
        "credible_interval": [round(ci[0], 4), round(ci[1], 4)],
        "evidence_refs": list(evidence_refs or []),
        "recommended": bool(recommended),
        "prior_adjustment": round(prior_adjustment, 4),
    }
    if sample_size is not None:
        opt["sample_size"] = max(0, int(sample_size))

    if display_text is not None and isinstance(display_text, str) and display_text.strip():
        opt["display_text"] = display_text.strip()
    else:
        opt["display_text"] = format_layman_display_label(opt)

    errors = validate_layman_option(opt)
    if errors:
        raise LaymanClarityError(f"Layman option validation failed: {'; '.join(errors)}")

    return opt
