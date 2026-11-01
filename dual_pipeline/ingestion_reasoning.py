"""Deterministic routing and bounded advisory reasoning for ingestion.

This module is a backward-compatible facade re-exporting all symbols from
dual_pipeline.ingestion_reasoning_core, preserving 100% symbol parity,
signatures, and type definitions.
"""

from __future__ import annotations

from .ingestion_reasoning_core import (
    ALLOWED_RECOMMENDATIONS,
    AMBIGUOUS_IDENTITY_OUTCOMES,
    CLASSIFICATION_ORDER,
    COMPLEXITY_ORDER,
    REASON_CODE_PATTERN,
    IngestionModelPolicy,
    IngestionProviderError,
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningError,
    IngestionReasoningPolicy,
    IngestionReasoningProvider,
    IngestionReasoningService,
    ProposalValidationError,
    ProviderFactory,
    build_ingestion_provider_body,
    build_ingestion_reasoning_decision,
    build_ingestion_reasoning_packet,
    create_ingestion_reasoning_proposal,
    ingestion_proposal_output_schema,
    ingestion_system_instruction,
    ingestion_user_payload,
    load_ingestion_reasoning_policy,
    validate_ingestion_proposal_output,
)

__all__ = [
    "ALLOWED_RECOMMENDATIONS",
    "AMBIGUOUS_IDENTITY_OUTCOMES",
    "CLASSIFICATION_ORDER",
    "COMPLEXITY_ORDER",
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
    "REASON_CODE_PATTERN",
    "build_ingestion_provider_body",
    "build_ingestion_reasoning_decision",
    "build_ingestion_reasoning_packet",
    "create_ingestion_reasoning_proposal",
    "ingestion_proposal_output_schema",
    "ingestion_system_instruction",
    "ingestion_user_payload",
    "load_ingestion_reasoning_policy",
    "validate_ingestion_proposal_output",
]
