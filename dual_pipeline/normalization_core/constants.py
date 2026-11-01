"""Constants and keys for candidate normalization."""
from __future__ import annotations

AUTHORITY_RANKS = {
    "authoritative_internal": 9,
    "governed_internal": 7,
    "derivative_internal": 5,
    "working_notes": 3,
    "external_first_party": 7,
    "external_partner": 6,
    "external_independent": 5,
    "external_contextual": 3,
}

SEMANTIC_CORRECTION_PACKAGE_KEY = "com.partner_advisory.nee.semantic-correction"
SEMANTIC_CORRECTION_CANDIDATE_KEY = (
    "com.partner_advisory.nee.semantic-correction-candidate-governance"
)
