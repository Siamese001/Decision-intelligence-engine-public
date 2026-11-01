"""Constants, errors, and table definitions for label quality governance."""
from __future__ import annotations

import re

from ..core import ZERO_HASH
from ..exceptions import DualPipelineValueError

LABEL_POLICY_ID = "nee-graph-labeling"
LABEL_POLICY_VERSION = "1.0.0"
LABEL_MIGRATION_CANDIDATE_CONTRACT_ID = (
    "urn:nee:dual-pipeline:graph-label-deterministic-candidate-plan:1.0.0"
)
LABEL_MIGRATION_CANDIDATE_STATUS = "CANDIDATE_ONLY_NOT_AUTHORIZED"
LABEL_MIGRATION_SCHEMA_ID = "0010_graph_labeling_hardening"
LABEL_MIGRATION_SCHEMA_STATES = frozenset(
    {"APPLY_TO_CANDIDATE", "ALREADY_INSTALLED"}
)
APPROVED_VALIDATION_STATES = frozenset({"approved", "accepted_legacy", "verified"})
CANONICAL_SOURCE_POSTURES = frozenset(
    {
        "authoritative_internal",
        "governed_internal",
        "derivative_internal",
        "working_notes",
        "external_first_party",
        "external_partner",
        "external_independent",
        "external_contextual",
    }
)
CANONICAL_EVIDENCE_DIRECTNESS = frozenset({"direct", "derivative", "contextual"})
AUDIENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$")

# These metrics must never increase between an active graph and a prepared
# release.  Existing debt can therefore be remediated incrementally without
# allowing a new release to make it worse.
NO_REGRESSION_METRICS = (
    "active_unregistered_alias_confidence_count",
    "hitl_assignment_incomplete_count",
    "invalid_audience_count",
    "unknown_audience_count",
    "missing_nonlegacy_extraction_run_count",
    "noncanonical_source_posture_count",
    "noncanonical_evidence_directness_count",
    "directional_high_score_count",
    "unparsed_legacy_assertion_count",
    "legacy_edge_type_count",
)

_CANDIDATE_BASE_TABLES = frozenset(
    {
        "assertion",
        "audit_event",
        "controlled_value_registry",
        "evidence_item",
        "governance_approval",
        "source",
    }
)
_CANDIDATE_HARDENING_TABLES = frozenset(
    {
        "assertion_audience",
        "assertion_label_state",
        "audience_registry",
        "evidence_label_state",
        "label_mapping_event",
        "label_policy_version",
        "legacy_label_mapping",
        "source_label_state",
    }
)
_CANDIDATE_REQUIRED_TABLES = _CANDIDATE_BASE_TABLES | _CANDIDATE_HARDENING_TABLES
_CANDIDATE_REQUIRED_VALUES = frozenset(
    {
        ("mapping_state", "deterministic_mapped"),
        ("source_kind", "legacy_source"),
        ("source_format", "legacy_unknown"),
        ("source_origin", "legacy_unknown"),
        ("validation_state", "accepted_legacy"),
        ("lifecycle_state", "unknown"),
    }
)
_APPROVAL_COLUMNS = (
    "approval_id",
    "approval_reference",
    "approval_type",
    "scope_json",
    "baseline_zip_sha256",
    "baseline_graph_sha256",
    "proposal_sha256",
    "decision_record_sha256",
    "card_proposal_sha256",
    "gate_o1_sha256",
    "approval_statement_sha256",
    "approved_at",
    "expires_at",
    "status",
    "approved_by",
    "release_version",
    "active",
    "created_at",
)


class LabelQualityError(DualPipelineValueError):
    """Raised when a label cannot be admitted safely."""


__all__ = [
    "APPROVED_VALIDATION_STATES",
    "AUDIENCE_PATTERN",
    "CANONICAL_EVIDENCE_DIRECTNESS",
    "CANONICAL_SOURCE_POSTURES",
    "LABEL_MIGRATION_CANDIDATE_CONTRACT_ID",
    "LABEL_MIGRATION_CANDIDATE_STATUS",
    "LABEL_MIGRATION_SCHEMA_ID",
    "LABEL_MIGRATION_SCHEMA_STATES",
    "LABEL_POLICY_ID",
    "LABEL_POLICY_VERSION",
    "LabelQualityError",
    "NO_REGRESSION_METRICS",
    "ZERO_HASH",
    "_APPROVAL_COLUMNS",
    "_CANDIDATE_BASE_TABLES",
    "_CANDIDATE_HARDENING_TABLES",
    "_CANDIDATE_REQUIRED_TABLES",
    "_CANDIDATE_REQUIRED_VALUES",
]

