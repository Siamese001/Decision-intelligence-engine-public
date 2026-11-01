"""Approval-bound release for the sealed Sherpa/status remediation control."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .combined_remediation_core import (
    _PAIR_FIELDS,
    _REQUIRED_CONTROL_KEYS,
    COMBINED_REMEDIATION_CONTROL_KEY,
    COMBINED_REMEDIATION_POLICY_VERSION,
    _aggregate_counts,
    _assert_active_binding,
    _assert_plan_digest,
    _pair_binding,
    _require_sha256,
    _require_text,
    apply_combined_remediation_plan,
    build_combined_remediation_plan,
    combined_remediation_control_from_package,
    prepare_combined_remediation_release,
    record_combined_remediation_release_events,
    validate_combined_remediation_control,
)
from .contracts import validate_artifact
from .core import (
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from .index import EmbeddingBackend, build_sidecar
from .project_status_reclassification import (
    PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION,
    apply_project_status_reclassification_plan,
    build_project_status_reclassification_plan,
    validate_project_status_reclassification_control,
)
from .release import ReleaseManager, _seal_receipt
from .source_retirement import (
    SOURCE_RETIREMENT_POLICY_VERSION,
    apply_source_retirement_plan,
    build_source_retirement_plan,
    validate_source_retirement_control,
)
from .telemetry import RunRecorder

__all__ = [
    "Any",
    "COMBINED_REMEDIATION_CONTROL_KEY",
    "COMBINED_REMEDIATION_POLICY_VERSION",
    "EmbeddingBackend",
    "PROJECT_STATUS_RECLASSIFICATION_POLICY_VERSION",
    "Path",
    "ReleaseManager",
    "RunRecorder",
    "SOURCE_RETIREMENT_POLICY_VERSION",
    "_PAIR_FIELDS",
    "_REQUIRED_CONTROL_KEYS",
    "_aggregate_counts",
    "_assert_active_binding",
    "_assert_plan_digest",
    "_pair_binding",
    "_require_sha256",
    "_require_text",
    "_seal_receipt",
    "apply_combined_remediation_plan",
    "apply_project_status_reclassification_plan",
    "apply_source_retirement_plan",
    "assert_safe_mutable_path",
    "build_combined_remediation_plan",
    "build_project_status_reclassification_plan",
    "build_sidecar",
    "build_source_retirement_plan",
    "combined_remediation_control_from_package",
    "digest",
    "exact_file_copy",
    "json",
    "prepare_combined_remediation_release",
    "record_combined_remediation_release_events",
    "sha256_file",
    "sqlite3",
    "sqlite_integrity",
    "stable_id",
    "tempfile",
    "utc_now",
    "validate_artifact",
    "validate_combined_remediation_control",
    "validate_project_status_reclassification_control",
    "validate_source_retirement_control",
    "write_json",
    "write_json_exclusive",
]
