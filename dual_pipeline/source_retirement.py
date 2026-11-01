"""Approval-bound removal of a bounded obsolete source representation.

Backward-compatible facade module delegating to dual_pipeline.source_retirement_core.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.database import sqlite3

from ._validators import sql_placeholders, table_exists
from .contracts import validate_artifact
from .core import (
    ZERO_HASH,
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
from .release import ReleaseManager, _seal_receipt
from .source_retirement_core import (
    _RELATED_TEXT_COLUMNS,
    _SUPERSESSION_DETACHMENT_TARGETS,
    _TARGET_KINDS,
    _TARGET_TABLES,
    SOURCE_RETIREMENT_CONTROL_KEY,
    SOURCE_RETIREMENT_POLICY_VERSION,
    _apply_source_retirement,
    _assert_dependencies_clear,
    _assert_plan_digest,
    _assert_related_text,
    _collect_targets,
    _delete_by_ids,
    _delete_canonical_routes,
    _derived_targets,
    _id_list,
    _in_clause,
    _placeholders,
    _query_ids,
    _require_text,
    _row_by_id,
    _row_hash,
    _seal_receipt,
    _source_scoped_ids,
    _table_exists,
    apply_source_retirement_plan,
    build_source_retirement_plan,
    prepare_source_retirement_release,
    record_source_retirement_release_events,
    source_retirement_control_from_package,
    validate_source_retirement_control,
)
from .telemetry import RunRecorder

__all__ = (
    "Any",
    "EmbeddingBackend",
    "Path",
    "ReleaseManager",
    "RunRecorder",
    "SOURCE_RETIREMENT_CONTROL_KEY",
    "SOURCE_RETIREMENT_POLICY_VERSION",
    "ZERO_HASH",
    "_RELATED_TEXT_COLUMNS",
    "_SUPERSESSION_DETACHMENT_TARGETS",
    "_TARGET_KINDS",
    "_TARGET_TABLES",
    "_apply_source_retirement",
    "_assert_dependencies_clear",
    "_assert_plan_digest",
    "_assert_related_text",
    "_collect_targets",
    "_delete_by_ids",
    "_delete_canonical_routes",
    "_derived_targets",
    "_id_list",
    "_in_clause",
    "_placeholders",
    "_query_ids",
    "_require_text",
    "_row_by_id",
    "_row_hash",
    "_seal_receipt",
    "_source_scoped_ids",
    "_table_exists",
    "annotations",
    "apply_source_retirement_plan",
    "assert_safe_mutable_path",
    "build_sidecar",
    "build_source_retirement_plan",
    "digest",
    "exact_file_copy",
    "json",
    "prepare_source_retirement_release",
    "record_source_retirement_release_events",
    "sha256_file",
    "source_retirement_control_from_package",
    "sql_placeholders",
    "sqlite3",
    "sqlite_integrity",
    "stable_id",
    "table_exists",
    "utc_now",
    "validate_artifact",
    "validate_source_retirement_control",
    "write_json",
    "write_json_exclusive",
)
