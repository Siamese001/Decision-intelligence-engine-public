"""Unified AST anti-hardcoding and SSOT configuration engine (`tools.ssot_config_engine`)."""

from __future__ import annotations

from .ast_rules import (
    DECLARED_PARAMETER_KEYS,
    GOVERNED_PARAMETER_FALLBACK_KEYS,
    PROHIBITED_DOMAIN_ENTITY_PATTERN,
    PROHIBITED_HITL_BADGE_PATTERN,
    PROHIBITED_MODEL_PATTERNS,
    SSOTASTViolation,
    TARGET_PARAMETER_FUNCTIONS,
    UnifiedSSOTASTVisitor,
    collect_declared_keys,
    is_ssot_exempt_comment,
    load_declared_parameter_keys,
)
from .discovery import (
    DEFAULT_GOVERNED_DIRS,
    discover_all_tracked_repo_files,
    discover_governed_python_files,
    is_test_or_excluded_path,
)

__all__ = [
    "DECLARED_PARAMETER_KEYS",
    "DEFAULT_GOVERNED_DIRS",
    "GOVERNED_PARAMETER_FALLBACK_KEYS",
    "PROHIBITED_DOMAIN_ENTITY_PATTERN",
    "PROHIBITED_HITL_BADGE_PATTERN",
    "PROHIBITED_MODEL_PATTERNS",
    "SSOTASTViolation",
    "TARGET_PARAMETER_FUNCTIONS",
    "UnifiedSSOTASTVisitor",
    "collect_declared_keys",
    "discover_all_tracked_repo_files",
    "discover_governed_python_files",
    "is_ssot_exempt_comment",
    "is_test_or_excluded_path",
    "load_declared_parameter_keys",
]
