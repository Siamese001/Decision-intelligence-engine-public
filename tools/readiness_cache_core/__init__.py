"""tools/readiness_cache_core

Modular readiness qualification cache, content-addressable memoization, and input manifests.
"""

from __future__ import annotations

from .constants import (
    CONFIG_FILES,
    ENV_VARS_TO_BIND,
    GENERATED_REPORT_PATTERNS,
    PACKAGES_TO_BIND,
    QUALITY_INPUT_PATTERNS,
    TEST_HELPER_PATTERNS,
    is_generated_report,
    is_quality_input,
)
from .fingerprint import (
    compute_qualification_fingerprint,
    compute_semantic_cache_key,
    compute_stage_fingerprint,
)
from .manifest import (
    InputManifest,
    build_input_manifest,
    validate_manifest_freshness,
)
from .storage import (
    CachedStageResult,
    clear_readiness_cache,
    get_cached_stage,
    get_stage_cache_dir,
    prune_stale_caches,
    save_cached_stage,
)

__all__ = [
    "CONFIG_FILES",
    "CachedStageResult",
    "ENV_VARS_TO_BIND",
    "GENERATED_REPORT_PATTERNS",
    "InputManifest",
    "PACKAGES_TO_BIND",
    "QUALITY_INPUT_PATTERNS",
    "TEST_HELPER_PATTERNS",
    "build_input_manifest",
    "clear_readiness_cache",
    "compute_qualification_fingerprint",
    "compute_semantic_cache_key",
    "compute_stage_fingerprint",
    "get_cached_stage",
    "get_stage_cache_dir",
    "is_generated_report",
    "is_quality_input",
    "prune_stale_caches",
    "save_cached_stage",
    "validate_manifest_freshness",
]
