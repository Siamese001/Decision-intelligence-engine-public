"""Sealed evaluation runner for governed query and intake paths.

Backward-compatible facade module delegating to dual_pipeline.evals.runner_core.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..completion import IngestionCompletionGuard
from ..contracts import create_artifact, validate_artifact
from ..core import (
    assert_safe_mutable_path,
    digest,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..drift_guard import (
    DriftError,
    assert_file_unchanged,
    assert_runtime_binding_unchanged,
    capture_runtime_binding,
)
from ..index import (
    CLASSIFICATION_RANK,
    HYBRID_RETRIEVAL_POLICY_DIGEST,
    EmbeddingBackend,
    HybridRetriever,
    render_retrieval_records,
)
from ..intake import IntakeEngine
from ..l2 import load_l2_config
from ..query import QueryEngine
from ..query_router import load_query_routing_policy
from ..release import ReleaseManager
from ..retrieval_release import RetrievalReleaseEvaluator
from ..retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
from ..telemetry import RunRecorder
from ..x1_quality import X1QualityMode, load_x1_quality_config
from .contracts import load_result, load_suite, resolve_repository_file, write_markdown_report
from .fakes import (
    SealedEvalProviderFactory,
    SealedEvalQualityProviderFactory,
)
from .runner_core import (
    FAILED_WORKSPACE_RETENTION_DAYS,
    PAIR_BINDING_FIELDS,
    WORKSPACE_RETENTION_FILE,
    EvaluationFailure,
    _aggregate,
    _assert_expected_records_present,
    _assert_operator_backend,
    _audiences,
    _audit,
    _bootstrap,
    _cache_binding_matches_backend,
    _case_artifact_path,
    _evaluation_policy_digests,
    _evaluation_workspace_lifecycle,
    _failure_result,
    _finalize_evaluation_workspace,
    _format_utc,
    _ingestion_case,
    _pair_binding,
    _parse_utc,
    _query_case,
    _remove_workspace,
    _requirement_expectations_met,
    _selected_evidence_authorization,
    _validated_template_pair_binding,
    compare_results,
    prune_expired_failed_workspaces,
    run_suite,
)

__all__ = [
    "Any",
    "AutomaticRetrievalReleaseSuiteBuilder",
    "CLASSIFICATION_RANK",
    "DriftError",
    "EmbeddingBackend",
    "EvaluationFailure",
    "FAILED_WORKSPACE_RETENTION_DAYS",
    "HYBRID_RETRIEVAL_POLICY_DIGEST",
    "HybridRetriever",
    "IngestionCompletionGuard",
    "IntakeEngine",
    "Iterable",
    "PAIR_BINDING_FIELDS",
    "Path",
    "QueryEngine",
    "ReleaseManager",
    "RetrievalReleaseEvaluator",
    "RunRecorder",
    "SealedEvalProviderFactory",
    "SealedEvalQualityProviderFactory",
    "WORKSPACE_RETENTION_FILE",
    "X1QualityMode",
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
    "annotations",
    "assert_file_unchanged",
    "assert_runtime_binding_unchanged",
    "assert_safe_mutable_path",
    "capture_runtime_binding",
    "compare_results",
    "create_artifact",
    "datetime",
    "digest",
    "load_l2_config",
    "load_query_routing_policy",
    "load_result",
    "load_suite",
    "load_x1_quality_config",
    "prune_expired_failed_workspaces",
    "re",
    "read_json",
    "render_retrieval_records",
    "replace",
    "resolve_repository_file",
    "run_suite",
    "sha256_file",
    "shutil",
    "stable_id",
    "timedelta",
    "timezone",
    "utc_now",
    "validate_artifact",
    "write_json",
    "write_json_exclusive",
    "write_markdown_report",
]
