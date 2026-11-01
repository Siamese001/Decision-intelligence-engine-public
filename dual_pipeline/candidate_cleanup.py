"""Deterministic cleanup of the active candidate-assertion backlog."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ._validators import row_hash
from .candidate_cleanup_core import (
    _ASSERTION_FIELDS,
    _CONTROLLED_VALUES,
    CLEANUP_POLICY_VERSION,
    PRESERVED_FUTURE_STATE_SOURCE_IDS,
    _assert_plan_digest,
    _assertion_state,
    _candidate_retrieval_count,
    _clean_qualifiers,
    _correlation,
    _disposition,
    _row_hash,
    apply_candidate_cleanup_plan,
    build_candidate_cleanup_plan,
    prepare_candidate_cleanup_release,
    record_candidate_cleanup_release_events,
)
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
from .telemetry import RunRecorder

__all__ = [
    "Any",
    "CLEANUP_POLICY_VERSION",
    "Counter",
    "EmbeddingBackend",
    "PRESERVED_FUTURE_STATE_SOURCE_IDS",
    "Path",
    "ReleaseManager",
    "RunRecorder",
    "ZERO_HASH",
    "_ASSERTION_FIELDS",
    "_CONTROLLED_VALUES",
    "_assert_plan_digest",
    "_assertion_state",
    "_candidate_retrieval_count",
    "_clean_qualifiers",
    "_correlation",
    "_disposition",
    "_row_hash",
    "_seal_receipt",
    "apply_candidate_cleanup_plan",
    "assert_safe_mutable_path",
    "build_candidate_cleanup_plan",
    "build_sidecar",
    "digest",
    "exact_file_copy",
    "json",
    "prepare_candidate_cleanup_release",
    "record_candidate_cleanup_release_events",
    "row_hash",
    "sha256_file",
    "sqlite3",
    "sqlite_integrity",
    "stable_id",
    "utc_now",
    "write_json",
    "write_json_exclusive",
]
