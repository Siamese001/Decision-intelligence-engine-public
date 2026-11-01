"""Constants for ingestion intake, governance, routing, and ledgering."""

from __future__ import annotations

INTAKE_STAGES = ["U0", "L1", "L0", "C0.1", "C0.2", "C0.3", "X1", "X2", "X3", "L4"]
EXTERNAL_CONCEPT_OUTCOME = "CREATE_EXTERNAL_CONCEPT"
EXTERNAL_CONCEPT_PROPOSAL_KEY = "com.partner_advisory.nee.external-concept-proposal"
IDENTITY_REVIEW_EXTENSION_KEY = "com.partner_advisory.nee.identity-review"
GRAPH_ADMISSION_EXTENSION_KEY = "com.partner_advisory.nee.graph-admission-decision"
INGESTION_DECISION_LEDGER_JSON = "ingestion_decision_ledger.json"
INGESTION_DECISION_LEDGER_ENGLISH_JSON = "ingestion_decision_ledger.english.json"
INGESTION_DECISION_LEDGER_MARKDOWN = "ingestion_decision_ledger.md"
A2A_SOURCE_SPAN_REVIEW_BINDING_KEY = "com.partner_advisory.nee.a2a-source-span-review-binding"
A2A_GUIDANCE_ANCHOR_KEY = "com.partner_advisory.nee.a2a-guidance-anchor-entity-id"
NEW_INTERNAL_ENTITY_KEY = "com.partner_advisory.nee.new-entity-proposal"
NEW_INTERNAL_ENTITY_DIGEST_KEY = "com.partner_advisory.nee.new-entity-proposal-digest"
SUPERSEDES_ASSERTION_KEY = "com.partner_advisory.nee.supersedes-assertion-id"
RETIRE_ASSERTION_KEY = "com.partner_advisory.nee.retire-assertion-id"
SEMANTIC_CORRECTION_CANDIDATE_KEY = (
    "com.partner_advisory.nee.semantic-correction-candidate-governance"
)
FACT_PROMOTION_READY_STATUSES = frozenset(
    {
        "candidate_evidence_bound_hitl_required",
        "candidate_evidence_selected_span_hitl_required",
    }
)
_A2A_FINALIZATION_BINDING_FIELDS = frozenset(
    {
        "receipt_id",
        "receipt_canonical_digest",
        "receipt_file_sha256",
        "package_file_sha256",
        "package_canonical_digest",
        "archive_root_id",
        "archive_binding_digest",
        "verification_state",
    }
)
