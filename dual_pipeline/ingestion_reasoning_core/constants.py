"""Constants and enumerations for ingestion reasoning."""
from __future__ import annotations

import re

COMPLEXITY_ORDER = {"low": 0, "normal": 1, "high": 2, "most_complex": 3}
CLASSIFICATION_ORDER = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}
AMBIGUOUS_IDENTITY_OUTCOMES = {
    "HITL_CONFIRM_ENTITY",
    "QUARANTINE_IDENTITY",
}
ALLOWED_RECOMMENDATIONS = {
    "CANDIDATE",
    "QUARANTINE",
    "REJECTED_NOOP",
    "HITL_REQUIRED",
}
REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
