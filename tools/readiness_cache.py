#!/usr/bin/env python3
"""Readiness Qualification Cache & Content-Addressable Stage Memoization (Wave 3).

Provides persistent, content-addressable memoization for verify_main_readiness.py stages.
Computes deterministic stage fingerprints based on immutable input manifests, tool script bytes,
command arguments, target file contents, git commit SHA, and uncommitted diffs.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.readiness_cache_core import (
    CONFIG_FILES,
    CachedStageResult,
    ENV_VARS_TO_BIND,
    GENERATED_REPORT_PATTERNS,
    InputManifest,
    PACKAGES_TO_BIND,
    QUALITY_INPUT_PATTERNS,
    TEST_HELPER_PATTERNS,
    build_input_manifest,
    clear_readiness_cache,
    compute_qualification_fingerprint,
    compute_semantic_cache_key,
    compute_stage_fingerprint,
    get_cached_stage,
    get_stage_cache_dir,
    is_generated_report,
    is_quality_input,
    prune_stale_caches,
    save_cached_stage,
    validate_manifest_freshness,
)
from tools.readiness_cache_core.fingerprint import (
    STAGE_DOMAIN_PATTERNS,
    _bind_candidate_inputs,
    _bind_command_sources,
    _bind_environment,
    _effective_targets,
    _local_dependencies,
    _read_import_names,
    _resolve_local_module,
    _resolve_tree_hash,
)
from tools.readiness_cache_core.manifest import (
    _compute_env_digest,
    _get_working_tree_diff,
    _hash_file_safe,
)

__all__ = [
    "CONFIG_FILES",
    "CachedStageResult",
    "ENV_VARS_TO_BIND",
    "GENERATED_REPORT_PATTERNS",
    "InputManifest",
    "PACKAGES_TO_BIND",
    "QUALITY_INPUT_PATTERNS",
    "STAGE_DOMAIN_PATTERNS",
    "TEST_HELPER_PATTERNS",
    "_bind_candidate_inputs",
    "_bind_command_sources",
    "_bind_environment",
    "_compute_env_digest",
    "_effective_targets",
    "_get_working_tree_diff",
    "_hash_file_safe",
    "_local_dependencies",
    "_read_import_names",
    "_resolve_local_module",
    "_resolve_tree_hash",
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


def main() -> int:
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Readiness cache inspector & pruning utility")
    parser.add_argument("--clear", action="store_true", help="Clear all stage cache entries")
    parser.add_argument("--prune", action="store_true", help="Prune stale cache files and expired receipts")
    parser.add_argument("--retention-days", type=int, default=7, help="Retention cutoff in days (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect what would be pruned without deleting")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    if args.clear:
        cnt = clear_readiness_cache(root)
        print(f"Cleared {cnt} readiness cache entry(ies).")
        return 0
    if args.prune or args.dry_run:
        res = prune_stale_caches(root, max_age_days=args.retention_days, dry_run=args.dry_run)
        mode = "Dry run" if args.dry_run else "Pruned"
        print(f"{mode}: {res.get('total_pruned', 0)} stale cache item(s) (retention: {args.retention_days} days)")
        for k, v in res.items():
            if k != "total_pruned" and v > 0:
                print(f"  • {k}: {v}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
