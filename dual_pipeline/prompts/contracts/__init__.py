"""
dual_pipeline/prompts/contracts/__init__.py

Package exposing declarative Output Contracts and MeetingArchetype.
"""
from __future__ import annotations

from .output1_contract import OUTPUT1_CONTRACT
from .output2_contract import (
    FORBIDDEN_APPENDIX_DISCLAIMERS,
    OUTPUT2_CONTRACT,
    PASSIVE_HEDGING_PATTERNS,
    ROLE_ANONYMIZATION_PATTERNS,
    WORD_SALAD_PATTERNS,
    WORD_SALAD_REGEX,
    validate_c21_depth_floors_and_anchors,
)
from .output3_contract import OUTPUT3_CONTRACT
from .output_contract import (
    MeetingArchetype,
    OutputContract,
    RubricDimension,
    SectionRequirement,
    SectionSpecification,
    get_dimension_floors_for_output,
    get_output_contract,
    is_document_pipeline_mode,
)

__all__ = [
    "MeetingArchetype",
    "SectionRequirement",
    "RubricDimension",
    "SectionSpecification",
    "OutputContract",
    "OUTPUT1_CONTRACT",
    "OUTPUT2_CONTRACT",
    "OUTPUT3_CONTRACT",
    "FORBIDDEN_APPENDIX_DISCLAIMERS",
    "PASSIVE_HEDGING_PATTERNS",
    "ROLE_ANONYMIZATION_PATTERNS",
    "WORD_SALAD_PATTERNS",
    "WORD_SALAD_REGEX",
    "validate_c21_depth_floors_and_anchors",
    "get_output_contract",
    "get_dimension_floors_for_output",
    "is_document_pipeline_mode",
]
