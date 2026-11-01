"""
dual_pipeline.validation
Token-Stream Syntax Repair & Strict Pydantic Gates.
Deterministic parsing and bounded schema enforcement.
"""

from .pydantic_gate import (
    DeterministicPDAParser,
    PydanticValidationError,
    TokenStreamJSONHealer,
    repair_json_tokens,
    validate_with_pydantic,
)

__all__ = [
    "repair_json_tokens",
    "validate_with_pydantic",
    "DeterministicPDAParser",
    "TokenStreamJSONHealer",
    "PydanticValidationError",
]
