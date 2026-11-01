"""Approval-bound administrative release for the card/facet refactor.

Backward-compatible facade module delegating to dual_pipeline.card_refactor_release_core.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from storage.database import sqlite3

from ._validators import row_hash
from .card_refactor_release_core import (
    _ROUTE_FIELDS,
    PLAN_TYPE,
    _apply_operation,
    _insert_audit_event,
    _legacy_card_state,
    _operation_counts,
    _resolve_or_stage_approval,
    _route_state,
    _row_hash,
    _validate_approval_row,
    apply_card_refactor_plan,
    assert_card_refactor_plan,
    build_card_refactor_plan,
    prepare_card_refactor_release,
    record_card_refactor_release_events,
    semantic_organization_change_set_artifact,
)
from .card_views import build_default_card_view_operations, validate_card_selector
from .contracts import create_artifact
from .core import (
    ZERO_HASH,
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    read_json,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from .facets import (
    CARDS_REFACTOR_MIGRATION_ID,
    CARDS_REFACTOR_POLICY_VERSION,
    SEMANTIC_DOMAIN_VOCABULARY,
    TOPIC_VOCABULARY,
    audit_cards_refactor_coverage,
    build_semantic_domain_operations,
    build_topic_operations,
    facet_schema_checksum,
    install_facet_schema,
    validate_facet_schema,
)
from .index import EmbeddingBackend, build_sidecar
from .release import ReleaseManager, _seal_receipt
from .telemetry import RunRecorder

__all__ = (
    "Any",
    "CARDS_REFACTOR_MIGRATION_ID",
    "CARDS_REFACTOR_POLICY_VERSION",
    "Counter",
    "EmbeddingBackend",
    "PLAN_TYPE",
    "Path",
    "ReleaseManager",
    "RunRecorder",
    "SEMANTIC_DOMAIN_VOCABULARY",
    "TOPIC_VOCABULARY",
    "ZERO_HASH",
    "_ROUTE_FIELDS",
    "_apply_operation",
    "_insert_audit_event",
    "_legacy_card_state",
    "_operation_counts",
    "_resolve_or_stage_approval",
    "_route_state",
    "_row_hash",
    "_seal_receipt",
    "_validate_approval_row",
    "annotations",
    "apply_card_refactor_plan",
    "assert_card_refactor_plan",
    "assert_safe_mutable_path",
    "audit_cards_refactor_coverage",
    "build_card_refactor_plan",
    "build_default_card_view_operations",
    "build_semantic_domain_operations",
    "build_sidecar",
    "build_topic_operations",
    "create_artifact",
    "digest",
    "exact_file_copy",
    "facet_schema_checksum",
    "install_facet_schema",
    "json",
    "prepare_card_refactor_release",
    "read_json",
    "record_card_refactor_release_events",
    "row_hash",
    "semantic_organization_change_set_artifact",
    "sha256_file",
    "sqlite3",
    "sqlite_integrity",
    "stable_id",
    "utc_now",
    "validate_card_selector",
    "validate_facet_schema",
    "write_json",
    "write_json_exclusive",
)
