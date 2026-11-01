"""
dual_pipeline.prompts package.

Single Source of Truth (SSOT) prompt templates and canonical evaluation contracts.
"""
from __future__ import annotations

from .compiler import CANONICAL_PROMPT_COMPILER, PromptCompiler
from .config import CANONICAL_PROMPT_CONFIG, EnginePromptConfigSSOT, JudgeExecutionConfig
from .contracts import (
    FORBIDDEN_APPENDIX_DISCLAIMERS,
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    PASSIVE_HEDGING_PATTERNS,
    ROLE_ANONYMIZATION_PATTERNS,
    WORD_SALAD_PATTERNS,
    WORD_SALAD_REGEX,
    MeetingArchetype,
    OutputContract,
    RubricDimension,
    SectionRequirement,
    SectionSpecification,
    validate_c21_depth_floors_and_anchors,
)
from .learned_priors import compile_learned_priors_block, inject_learned_priors_into_metadata
from .registry import CANONICAL_PROMPT_REGISTRY, PromptRegistry

__all__ = [
    "PromptRegistry",
    "CANONICAL_PROMPT_REGISTRY",
    "PromptCompiler",
    "CANONICAL_PROMPT_COMPILER",
    "CANONICAL_PROMPT_CONFIG",
    "EnginePromptConfigSSOT",
    "JudgeExecutionConfig",
    "MeetingArchetype",
    "OutputContract",
    "RubricDimension",
    "SectionRequirement",
    "SectionSpecification",
    "OUTPUT1_CONTRACT",
    "OUTPUT2_CONTRACT",
    "OUTPUT3_CONTRACT",
    "FORBIDDEN_APPENDIX_DISCLAIMERS",
    "PASSIVE_HEDGING_PATTERNS",
    "ROLE_ANONYMIZATION_PATTERNS",
    "WORD_SALAD_PATTERNS",
    "WORD_SALAD_REGEX",
    "validate_c21_depth_floors_and_anchors",
    "compile_learned_priors_block",
    "inject_learned_priors_into_metadata",
]
