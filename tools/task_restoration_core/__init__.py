"""tools/task_restoration_core/__init__.py

Core library for Antigravity task content restoration and wave verification.
"""
from __future__ import annotations

from .constants import (
    SEVEN_TASKS,
    VALID_CATEGORIES,
    VALID_DISPOSITIONS,
    VALID_TARGET_MODES,
    WAVE_INPUT_FILES,
    WAVE_TEST_FILES,
)
from .inventory import (
    verify_procedure_deletion_defense,
    verify_requirements_inventory,
)
from .operational import (
    compute_canonical_digest,
    compute_file_sha256,
    get_git_info,
    measure_operational_effects,
)

__all__ = [
    "SEVEN_TASKS",
    "VALID_CATEGORIES",
    "VALID_DISPOSITIONS",
    "VALID_TARGET_MODES",
    "WAVE_INPUT_FILES",
    "WAVE_TEST_FILES",
    "compute_canonical_digest",
    "compute_file_sha256",
    "get_git_info",
    "measure_operational_effects",
    "verify_procedure_deletion_defense",
    "verify_requirements_inventory",
]
