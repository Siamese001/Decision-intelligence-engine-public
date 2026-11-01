#!/usr/bin/env python3
"""Validate NEE-KG-DUAL-PIPELINE-01 Wave 1 contracts and fixtures.

Backward-compatibility facade delegating to `dual_pipeline.contract_validation`.
Preserves 100% public symbol API, CLI flags, arguments, exit codes, and output contracts.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dual_pipeline.contract_validation import (
    ARTIFACT_REQUIRED_FIELDS,
    COMMON_ID,
    DEFAULT_CONTRACT_ROOT,
    ROOT,
    SCHEMA_DIALECT,
    SENSITIVE_OTEL_KEY,
    SUB_OBJECT_SCHEMA_IDS,
    ContractError,
    SchemaRegistry,
    ValidationFailure,
    ValidationIssue,
    _type_matches,
    _valid_format,
    canonical_artifact_digest,
    canonical_json_bytes,
    fail,
    iter_refs,
    join_path,
    load_json,
    main,
    require_unique_ids,
    run_validation,
    sha256_file,
    validate_artifact,
    validate_eval_measurement_semantics,
    validate_eval_suite_v3_semantics,
    validate_fixtures,
    validate_query_route_eval_result_semantics,
    validate_query_route_eval_suite_semantics,
    validate_schema,
    validate_semantics,
    validate_strategic_profile_semantics,
    verify_canonical_digest,
    verify_inventory,
    write_report,
)

__all__ = [
    "ARTIFACT_REQUIRED_FIELDS",
    "COMMON_ID",
    "SUB_OBJECT_SCHEMA_IDS",
    "DEFAULT_CONTRACT_ROOT",
    "ROOT",
    "SCHEMA_DIALECT",
    "SENSITIVE_OTEL_KEY",
    "ContractError",
    "SchemaRegistry",
    "ValidationFailure",
    "ValidationIssue",
    "_type_matches",
    "_valid_format",
    "canonical_artifact_digest",
    "canonical_json_bytes",
    "fail",
    "iter_refs",
    "join_path",
    "load_json",
    "main",
    "require_unique_ids",
    "run_validation",
    "sha256_file",
    "validate_artifact",
    "validate_eval_measurement_semantics",
    "validate_eval_suite_v3_semantics",
    "validate_fixtures",
    "validate_query_route_eval_result_semantics",
    "validate_query_route_eval_suite_semantics",
    "validate_schema",
    "validate_semantics",
    "validate_strategic_profile_semantics",
    "verify_canonical_digest",
    "verify_inventory",
    "write_report",
]

if __name__ == "__main__":
    main()
