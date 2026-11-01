"""
dual_pipeline/strategic_reasoning_core/__init__.py

Core components and constants for strategic reasoning.
"""
from __future__ import annotations

from .channels import (
    build_strategic_channels,
    create_strategic_evidence_artifact,
    create_strategic_reasoning_output,
    strategic_role_by_record,
)
from .constants import (
    _CHANNEL_BY_ROLE,
    APPLICABILITY_SCOPES,
    AUTHORITY_SCOPES,
    KNOWLEDGE_ROLES,
    MODALITIES,
    PROFILE_FIELDS,
    REALIZATION_STATES,
    STRATEGIC_ANSWER_USES,
    STRATEGIC_CHANNELS,
    STRATEGIC_PROFILE_QUALIFIER,
)
from .profiling import (
    _explicit_profile,
    _profile,
    answer_use_for_profile,
    classify_constraint_category,
    create_strategic_profile_artifact,
    explicit_strategic_profile,
    extract_quantitative_constraints,
    hydrate_quantitative_metadata,
    infer_object_literal_type,
    is_constraint_claim,
    strategic_profile_for_record,
    validate_strategic_profile,
)

__all__ = [
    "APPLICABILITY_SCOPES",
    "AUTHORITY_SCOPES",
    "KNOWLEDGE_ROLES",
    "MODALITIES",
    "PROFILE_FIELDS",
    "REALIZATION_STATES",
    "STRATEGIC_ANSWER_USES",
    "STRATEGIC_CHANNELS",
    "STRATEGIC_PROFILE_QUALIFIER",
    "_CHANNEL_BY_ROLE",
    "_explicit_profile",
    "_profile",
    "answer_use_for_profile",
    "build_strategic_channels",
    "classify_constraint_category",
    "create_strategic_evidence_artifact",
    "create_strategic_profile_artifact",
    "create_strategic_reasoning_output",
    "explicit_strategic_profile",
    "extract_quantitative_constraints",
    "hydrate_quantitative_metadata",
    "infer_object_literal_type",
    "is_constraint_claim",
    "strategic_profile_for_record",
    "strategic_role_by_record",
    "validate_strategic_profile",
]
