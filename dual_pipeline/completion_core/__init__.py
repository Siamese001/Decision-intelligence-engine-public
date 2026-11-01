"""Completion and persistent directive controls core package."""
from __future__ import annotations

from .constants import (
    _BASELINE_PAIR_FIELDS,
    _BGE_M3_BACKENDS,
    _BLOCK_REASON_PATTERN,
    _EMBEDDING_TARGET_FIELDS,
    _SHA256_PATTERN,
    DIRECTIVE_ID_PATTERN,
    DIRECTIVE_SCHEMA_VERSION,
    DIRECTIVE_STATUSES,
    RELEASE_STAGES,
    TERMINAL_STATUSES,
    _is_nonblank_string,
    _is_nonnegative_int,
    _is_sha256,
    _package_path_key,
)
from .guard import (
    IngestionCompletionGuard,
)
from .verification import (
    _seal_directive,
    _verify_approval_binding,
    _verify_approval_history,
    _verify_completion,
    _verify_directive,
    _verify_embedding_target,
    _verify_package_bindings,
    _verify_pair_binding,
    _verify_state_history,
    _verify_terminal_state,
)

__all__ = [
    "DIRECTIVE_ID_PATTERN",
    "DIRECTIVE_SCHEMA_VERSION",
    "DIRECTIVE_STATUSES",
    "IngestionCompletionGuard",
    "RELEASE_STAGES",
    "TERMINAL_STATUSES",
    "_BASELINE_PAIR_FIELDS",
    "_BGE_M3_BACKENDS",
    "_BLOCK_REASON_PATTERN",
    "_EMBEDDING_TARGET_FIELDS",
    "_SHA256_PATTERN",
    "_is_nonblank_string",
    "_is_nonnegative_int",
    "_is_sha256",
    "_package_path_key",
    "_seal_directive",
    "_verify_approval_binding",
    "_verify_approval_history",
    "_verify_completion",
    "_verify_directive",
    "_verify_embedding_target",
    "_verify_package_bindings",
    "_verify_pair_binding",
    "_verify_state_history",
    "_verify_terminal_state",
]
