"""Core implementation for source retirement operations."""
from __future__ import annotations

from .apply import (
    _apply_source_retirement,
    _delete_by_ids,
    _delete_canonical_routes,
    apply_source_retirement_plan,
)
from .constants import (
    _RELATED_TEXT_COLUMNS,
    _SUPERSESSION_DETACHMENT_TARGETS,
    _TARGET_KINDS,
    _TARGET_TABLES,
    SOURCE_RETIREMENT_CONTROL_KEY,
    SOURCE_RETIREMENT_POLICY_VERSION,
    _id_list,
    _placeholders,
    _require_text,
    _row_hash,
    _table_exists,
)
from .control import (
    source_retirement_control_from_package,
    validate_source_retirement_control,
)
from .plan import (
    _assert_plan_digest,
    build_source_retirement_plan,
)
from .release import (
    _seal_receipt,
    prepare_source_retirement_release,
    record_source_retirement_release_events,
)
from .targets import (
    _assert_dependencies_clear,
    _assert_related_text,
    _collect_targets,
    _derived_targets,
    _in_clause,
    _query_ids,
    _row_by_id,
    _source_scoped_ids,
)

__all__ = [
    "SOURCE_RETIREMENT_CONTROL_KEY",
    "SOURCE_RETIREMENT_POLICY_VERSION",
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
    "apply_source_retirement_plan",
    "build_source_retirement_plan",
    "prepare_source_retirement_release",
    "record_source_retirement_release_events",
    "source_retirement_control_from_package",
    "validate_source_retirement_control",
]
