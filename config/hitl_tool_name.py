#!/usr/bin/env python3
"""Single Source of Truth (SSOT) tool name definitions for operator questioning.

Enforces that `ask_user_question` is the sole canonical tool name across the entire
repository for presenting interactive multiple-choice questions to human operators.
All variants (`ask_question`, `ask_user`, `ask_human`, etc.) are strictly deprecated.
"""

from __future__ import annotations

import warnings
from typing import FrozenSet, Optional

# ==============================================================================
# CANONICAL SSOT TOOL NAME
# ==============================================================================

ASK_USER_QUESTION: str = "ask_user_question"
HOST_RUNTIME_TOOL_NAME: str = "ask_question"

# Set of all valid canonical tool names for human operator questioning
CANONICAL_TOOL_NAMES: FrozenSet[str] = frozenset({ASK_USER_QUESTION})

# Set of valid runtime execution primitives (canonical governance SSOT + host runtime tool)
RUNTIME_EXECUTION_PRIMITIVES: FrozenSet[str] = frozenset(
    {ASK_USER_QUESTION, HOST_RUNTIME_TOOL_NAME}
)

# ==============================================================================
# DEPRECATED TOOL NAMES
# ==============================================================================

DEPRECATED_NAMES: FrozenSet[str] = frozenset(
    {
        "ask_question",
        "ask_user",
        "ask_human",
        "prompt_user",
        "ask_operator",
        "request_human_input",
    }
)


def is_canonical_tool_name(name: Optional[str]) -> bool:
    """Return True if the given tool name matches the canonical SSOT name."""
    if not name or not isinstance(name, str):
        return False
    return name.strip() == ASK_USER_QUESTION


def is_runtime_execution_primitive(name: Optional[str]) -> bool:
    """Return True if the given tool name is a recognized runtime execution primitive."""
    if not name or not isinstance(name, str):
        return False
    return name.strip() in RUNTIME_EXECUTION_PRIMITIVES


def is_deprecated_tool_name(name: Optional[str]) -> bool:
    """Return True if the given tool name is a known deprecated variant."""
    if not name or not isinstance(name, str):
        return False
    cleaned = name.strip()
    return cleaned in DEPRECATED_NAMES or cleaned.lower() in DEPRECATED_NAMES


def get_tool_name_deprecation_message(deprecated_name: str) -> str:
    """Generate a descriptive deprecation error message."""
    return (
        f"Tool name '{deprecated_name}' is deprecated. The sole canonical SSOT name "
        f"for presenting interactive questions to the operator is '{ASK_USER_QUESTION}'."
    )


def canonicalize_tool_name(name: str, emit_warning: bool = True) -> str:
    """Validate or map a tool name to the canonical SSOT tool name.

    If name is canonical, returns it directly.
    If name is a known deprecated variant, optionally emits a DeprecationWarning
    and returns the canonical name.
    If name is unknown, raises ValueError.
    """
    if not isinstance(name, str):
        raise TypeError(f"Tool name must be str, got {type(name).__name__}")

    stripped = name.strip()
    if stripped == ASK_USER_QUESTION:
        return ASK_USER_QUESTION

    if is_deprecated_tool_name(stripped):
        if emit_warning:
            warnings.warn(
                get_tool_name_deprecation_message(stripped),
                category=DeprecationWarning,
                stacklevel=2,
            )
        return ASK_USER_QUESTION

    raise ValueError(
        f"Invalid tool name '{name}'. Expected canonical name '{ASK_USER_QUESTION}'."
    )


def validate_tool_name(name: str) -> None:
    """Strictly assert that a tool name is the canonical SSOT name, raising ValueError otherwise."""
    if not is_canonical_tool_name(name):
        raise ValueError(
            f"Non-canonical tool name '{name}'. Sole allowed tool name is '{ASK_USER_QUESTION}'."
        )
