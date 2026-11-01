"""Constants and controlled vocabularies for candidate assertion cleanup."""
from __future__ import annotations

CLEANUP_POLICY_VERSION = "2026-07-27.1"
PRESERVED_FUTURE_STATE_SOURCE_IDS = frozenset(
    {
        "source-architecture-artifact-4cfa9ebee2a2105d68b74414",
        "source-architecture-artifact-de42095dd92c2496149f0b14",
        "source-sherpa-architecture-context-pack-1211a650e81c881226ee0a33",
    }
)
_CONTROLLED_VALUES = {
    "validation_pending": (
        "Source-backed assertion held out of candidate retrieval pending its "
        "named validation task; this is not a rejection of the underlying claim."
    ),
    "evidence_only": (
        "Source-backed assertion retained as evidence only and excluded from "
        "candidate and canonical retrieval until a new governed validation task exists."
    ),
}
_ASSERTION_FIELDS = (
    "assertion_id",
    "canonical_status",
    "currentness_state",
    "canonical_eligible",
    "retrieval_eligible",
    "validation_priority",
    "validation_state",
    "primary_source_id",
    "qualifiers_json",
)
