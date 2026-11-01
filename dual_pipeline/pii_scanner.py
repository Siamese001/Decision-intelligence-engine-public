"""
dual_pipeline/pii_scanner.py

PII detection, redaction, and fail-closed screening for prompt injection prevention.
Screens transcript text for SSN, credit cards, cleartext passwords, bearer tokens,
and API keys before LLM ingestion.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger("dual_pipeline.pii_scanner")


class PIIViolationError(ValueError):
    """Raised when unredacted PII is detected and fail_closed mode is enforced."""


@dataclass(frozen=True)
class PIIFinding:
    category: str
    matched_text: str
    start: int
    end: int
    replacement: str


# Regex definitions for common sensitive tokens
SSN_PATTERN = re.compile(
    r"\b(?!000|666|9\d{2})\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b"
)

# Standard credit card numbers (Visa, Mastercard, Amex, Discover) with optional dashes/spaces
CREDIT_CARD_PATTERN = re.compile(
    r"\b(?:4\d{3}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}|5[1-5]\d{2}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}|3[47]\d{2}[- ]?\d{6}[- ]?\d{5}|6011[- ]?\d{4}[- ]?\d{4}[- ]?\d{4})\b"
)

# Credentials, cleartext passwords, and API keys
CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|secret_key|private_key)\s*[:=]\s*['\"]?([A-Za-z0-9!@#$%^&*()_+\-={}\[\]|:;<>?,./]{6,})['\"]?"
)

API_KEY_PATTERN = re.compile(
    r"\b(?:sk-[a-zA-Z0-9]{32,}|ghp_[a-zA-Z0-9]{36}|xox[baprs]-[0-9a-zA-Z]{10,48}|AIza[0-9A-Za-z-_]{35})\b"
)


def scan_pii(text: str) -> List[PIIFinding]:
    """Scans text for SSN, credit card numbers, passwords, and sensitive keys."""
    if not text:
        return []
    findings: List[PIIFinding] = []

    for m in SSN_PATTERN.finditer(text):
        findings.append(
            PIIFinding(
                category="SSN",
                matched_text=m.group(0),
                start=m.start(),
                end=m.end(),
                replacement="[REDACTED_SSN]",
            )
        )

    for m in CREDIT_CARD_PATTERN.finditer(text):
        findings.append(
            PIIFinding(
                category="CREDIT_CARD",
                matched_text=m.group(0),
                start=m.start(),
                end=m.end(),
                replacement="[REDACTED_CREDIT_CARD]",
            )
        )

    for m in CREDENTIAL_PATTERN.finditer(text):
        findings.append(
            PIIFinding(
                category="PASSWORD",
                matched_text=m.group(1),
                start=m.start(1),
                end=m.end(1),
                replacement="[REDACTED_PASSWORD]",
            )
        )

    for m in API_KEY_PATTERN.finditer(text):
        findings.append(
            PIIFinding(
                category="API_KEY",
                matched_text=m.group(0),
                start=m.start(),
                end=m.end(),
                replacement="[REDACTED_API_KEY]",
            )
        )

    # Sort in ascending order by start index
    findings.sort(key=lambda f: f.start)
    return findings


def redact_pii(text: str) -> Tuple[str, List[PIIFinding]]:
    """
    Redacts all identified PII items in text, returning the sanitized string and list of findings.
    Processes string in reverse order to keep character offsets stable.
    """
    if not text:
        return "", []
    findings = scan_pii(text)
    if not findings:
        return text, []

    # Sort descending by start to avoid invalidating offsets
    desc_findings = sorted(findings, key=lambda f: f.start, reverse=True)
    redacted = text
    for f in desc_findings:
        redacted = redacted[:f.start] + f.replacement + redacted[f.end:]

    return redacted, findings


def enforce_pii_clean(text: str, fail_closed: bool = False) -> str:
    """
    Screens text for PII. If fail_closed is True and PII is found, raises PIIViolationError.
    Otherwise returns the redacted text with all PII replaced by tokens.
    """
    sanitized, findings = redact_pii(text)
    if findings and fail_closed:
        cats = [f.category for f in findings]
        raise PIIViolationError(
            f"PII_VIOLATION_FAIL_CLOSED: Prohibited sensitive items detected in input stream: {cats}"
        )
    return sanitized
