"""tools/scheduled_tasks_core/ssot_pointers.py

Canonical re-export of SSOT pointer utilities loaded from config/scheduled_tasks/task_registry.json.
"""
from __future__ import annotations

from tools.task_restoration_core.ssot_pointers import (
    CONSOLIDATED_TASK_ALIASES,
    MAX_POINTER_WORDS,
    POINTER_REGEX,
    POINTER_TEMPLATE,
    RETIRED_SIDECAR_DIRS,
    TASK_SIDECAR_MAP,
    extract_target_path,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
    resolve_canonical_task_id,
    resolve_primary_repo_root,
    sanitize_reconciliation_manifest,
)

__all__ = [
    "CONSOLIDATED_TASK_ALIASES",
    "MAX_POINTER_WORDS",
    "POINTER_REGEX",
    "POINTER_TEMPLATE",
    "RETIRED_SIDECAR_DIRS",
    "TASK_SIDECAR_MAP",
    "extract_target_path",
    "generate_ssot_pointer_prompt",
    "is_valid_pointer_prompt",
    "resolve_canonical_task_id",
    "resolve_primary_repo_root",
    "sanitize_reconciliation_manifest",
]
