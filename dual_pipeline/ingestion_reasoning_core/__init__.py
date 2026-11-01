"""Core package for ingestion reasoning and deterministic routing."""

from __future__ import annotations

from .advisory import (
    _call_payload,
    _correlation,
    _validate_reason_codes,
    build_ingestion_provider_body,
    build_ingestion_reasoning_packet,
    create_ingestion_reasoning_proposal,
    ingestion_proposal_output_schema,
    ingestion_system_instruction,
    ingestion_user_payload,
    validate_ingestion_proposal_output,
)
from .constants import (
    ALLOWED_RECOMMENDATIONS,
    AMBIGUOUS_IDENTITY_OUTCOMES,
    CLASSIFICATION_ORDER,
    COMPLEXITY_ORDER,
    REASON_CODE_PATTERN,
)
from .errors import (
    IngestionProviderError,
    IngestionReasoningError,
    ProposalValidationError,
)
from .models import (
    IngestionModelPolicy,
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningPolicy,
    IngestionReasoningProvider,
    ProviderFactory,
    _require_exact_keys,
)
from .policy import (
    _highest_classification,
    _promote,
    build_ingestion_reasoning_decision,
    load_ingestion_reasoning_policy,
)
from .selective import (
    build_deterministic_recommendation,
    merge_advisory_and_deterministic_proposals,
    merge_advisory_subpacket,
    partition_candidates_by_complexity,
    prepare_selective_candidates,
)
from .service import IngestionReasoningService

__all__ = [
    "ALLOWED_RECOMMENDATIONS",
    "AMBIGUOUS_IDENTITY_OUTCOMES",
    "CLASSIFICATION_ORDER",
    "COMPLEXITY_ORDER",
    "REASON_CODE_PATTERN",
    "IngestionModelPolicy",
    "IngestionProviderError",
    "IngestionProviderRequest",
    "IngestionProviderResponse",
    "IngestionReasoningError",
    "IngestionReasoningPolicy",
    "IngestionReasoningProvider",
    "IngestionReasoningService",
    "ProposalValidationError",
    "ProviderFactory",
    "_call_payload",
    "_correlation",
    "_highest_classification",
    "_promote",
    "_require_exact_keys",
    "_validate_reason_codes",
    "build_ingestion_provider_body",
    "build_ingestion_reasoning_decision",
    "build_ingestion_reasoning_packet",
    "create_ingestion_reasoning_proposal",
    "ingestion_proposal_output_schema",
    "ingestion_system_instruction",
    "ingestion_user_payload",
    "load_ingestion_reasoning_policy",
    "merge_advisory_and_deterministic_proposals",
    "partition_candidates_by_complexity",
    "validate_ingestion_proposal_output",
]
