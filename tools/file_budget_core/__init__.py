"""
Core components for Multi-Dimensional Modularity Index (MMI) & file budget governance.
"""
from __future__ import annotations

from .attestation import (
    ATTESTATION_TOLERANCE,
    verify_attestations_against_ast,
)
from .mmi import (
    DEFAULT_MAX_COGNITIVE_COMPLEXITY,
    DEFAULT_MAX_EXTERNAL_IMPORTS,
    ModuleMetrics,
    calculate_ast_metrics,
    count_lines,
    generate_mmi_snapshot,
)
from .ratchet import (
    RatchetMonotonicityViolationError,
    collect_oversized_modules,
    generate_baseline,
    load_ratchet_baseline,
)

from .policy import (
    CandidatePolicy,
    PolicyVerdict,
    evaluate_module_file,
    evaluate_module_source,
    is_ast_pure_declarative,
    load_candidate_policy,
    validate_exempt_prefixes,
)

from .shadow import (
    build_legacy_dispositions,
    evaluate_legacy_module,
    evaluate_regression_corpus,
    generate_shadow_comparison,
)

__all__ = [
    "ModuleMetrics",
    "calculate_ast_metrics",
    "count_lines",
    "generate_mmi_snapshot",
    "verify_attestations_against_ast",
    "load_ratchet_baseline",
    "collect_oversized_modules",
    "generate_baseline",
    "RatchetMonotonicityViolationError",
    "ATTESTATION_TOLERANCE",
    "DEFAULT_MAX_COGNITIVE_COMPLEXITY",
    "DEFAULT_MAX_EXTERNAL_IMPORTS",
    "CandidatePolicy",
    "PolicyVerdict",
    "evaluate_module_file",
    "evaluate_module_source",
    "is_ast_pure_declarative",
    "load_candidate_policy",
    "build_legacy_dispositions",
    "evaluate_legacy_module",
    "evaluate_regression_corpus",
    "generate_shadow_comparison",
]

