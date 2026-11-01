"""
dual_pipeline.contract_validation

Modularized contract validation package for NEE-KG-DUAL-PIPELINE-01 contracts,
schemas, canonical digests, and semantic governance invariants.
"""
from __future__ import annotations

from .digests import (
    canonical_artifact_digest,
    canonical_json_bytes,
    join_path,
    load_json,
    sha256_file,
)
from .fixtures import (
    validate_fixtures,
    verify_canonical_digest,
    verify_inventory,
)
from .issues import (
    ContractError,
    ValidationFailure,
    ValidationIssue,
    fail,
)
from .registry import (
    ARTIFACT_REQUIRED_FIELDS,
    COMMON_ID,
    DEFAULT_CONTRACT_ROOT,
    ROOT,
    SCHEMA_DIALECT,
    SENSITIVE_OTEL_KEY,
    SUB_OBJECT_SCHEMA_IDS,
    SchemaRegistry,
    iter_refs,
)
from .runner import (
    main,
    run_validation,
    write_report,
)
from .semantics import (
    require_unique_ids,
    validate_eval_measurement_semantics,
    validate_eval_suite_v3_semantics,
    validate_query_route_eval_result_semantics,
    validate_query_route_eval_suite_semantics,
    validate_semantics as _base_validate_semantics,
    validate_strategic_profile_semantics,
)
from .active_ingestion_semantics import validate_active_ingestion_semantics
from typing import Any


def validate_semantics(artifact: dict[str, Any]) -> None:
    _base_validate_semantics(artifact)
    validate_active_ingestion_semantics(artifact.get("contract_id", ""), artifact, fail)

from .validator import (
    _type_matches,
    _valid_format,
    validate_artifact,
    validate_schema,
)

__all__ = [
    "ContractError",
    "ValidationIssue",
    "ValidationFailure",
    "fail",
    "canonical_json_bytes",
    "canonical_artifact_digest",
    "sha256_file",
    "load_json",
    "join_path",
    "ROOT",
    "DEFAULT_CONTRACT_ROOT",
    "SCHEMA_DIALECT",
    "COMMON_ID",
    "SUB_OBJECT_SCHEMA_IDS",
    "ARTIFACT_REQUIRED_FIELDS",
    "SENSITIVE_OTEL_KEY",
    "SchemaRegistry",
    "iter_refs",
    "_type_matches",
    "_valid_format",
    "validate_schema",
    "validate_artifact",
    "require_unique_ids",
    "validate_strategic_profile_semantics",
    "validate_eval_measurement_semantics",
    "validate_query_route_eval_suite_semantics",
    "validate_query_route_eval_result_semantics",
    "validate_eval_suite_v3_semantics",
    "validate_semantics",
    "verify_canonical_digest",
    "verify_inventory",
    "validate_fixtures",
    "run_validation",
    "write_report",
    "main",
]
