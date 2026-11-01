"""dual_pipeline/source_classification

Package exposing source classification models, orthogonal axes, authority SSOT,
and format capability registry for Output 3 evidence fidelity.
"""
from __future__ import annotations

from dual_pipeline.source_classification.capability_registry import (
    FormatCapability,
    SupportStatus,
    audit_matrix,
    get_capability_by_ext,
    get_capability_by_format_id,
    get_capability_by_mime,
    get_diagram_extensions,
    is_format_supported,
)
from dual_pipeline.source_classification.classifier import (
    classify_source,
)
from dual_pipeline.source_classification.models import (
    AUTHORITY_RANK_WEIGHTS,
    ApprovalState,
    ClassificationBasis,
    MediaModality,
    SemanticFamily,
    SourceProfile,
    SourceSection,
    adapt_historical_authority_rank,
    canonical_authority_weight,
    is_sufficient_authority,
)

__all__ = [
    "AUTHORITY_RANK_WEIGHTS",
    "ApprovalState",
    "ClassificationBasis",
    "FormatCapability",
    "MediaModality",
    "SemanticFamily",
    "SourceProfile",
    "SourceSection",
    "SupportStatus",
    "adapt_historical_authority_rank",
    "audit_matrix",
    "canonical_authority_weight",
    "classify_source",
    "get_capability_by_ext",
    "get_capability_by_format_id",
    "get_capability_by_mime",
    "get_diagram_extensions",
    "is_format_supported",
    "is_sufficient_authority",
]
