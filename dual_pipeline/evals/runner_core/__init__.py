"""Core implementation for sealed evaluation runner."""
from __future__ import annotations

from .compare import (
    _aggregate,
    compare_results,
)
from .constants import (
    FAILED_WORKSPACE_RETENTION_DAYS,
    PAIR_BINDING_FIELDS,
    WORKSPACE_RETENTION_FILE,
    EvaluationFailure,
    _assert_expected_records_present,
    _assert_operator_backend,
    _audiences,
    _cache_binding_matches_backend,
    _case_artifact_path,
    _evaluation_policy_digests,
    _failure_result,
    _format_utc,
    _pair_binding,
    _parse_utc,
    _requirement_expectations_met,
    _validated_template_pair_binding,
)
from .ingestion_case import _ingestion_case
from .query_case import (
    _query_case,
    _selected_evidence_authorization,
)
from .suite import run_suite
from .workspace import (
    _audit,
    _bootstrap,
    _evaluation_workspace_lifecycle,
    _finalize_evaluation_workspace,
    _remove_workspace,
    prune_expired_failed_workspaces,
)

__all__ = [
    "EvaluationFailure",
    "FAILED_WORKSPACE_RETENTION_DAYS",
    "PAIR_BINDING_FIELDS",
    "WORKSPACE_RETENTION_FILE",
    "_aggregate",
    "_assert_expected_records_present",
    "_assert_operator_backend",
    "_audiences",
    "_audit",
    "_bootstrap",
    "_cache_binding_matches_backend",
    "_case_artifact_path",
    "_evaluation_policy_digests",
    "_evaluation_workspace_lifecycle",
    "_failure_result",
    "_finalize_evaluation_workspace",
    "_format_utc",
    "_ingestion_case",
    "_pair_binding",
    "_parse_utc",
    "_query_case",
    "_remove_workspace",
    "_requirement_expectations_met",
    "_selected_evidence_authorization",
    "_validated_template_pair_binding",
    "compare_results",
    "prune_expired_failed_workspaces",
    "run_suite",
]
