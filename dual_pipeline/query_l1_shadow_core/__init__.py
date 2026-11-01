"""Core subpackage for governed query L1 shadow execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..contracts import create_versioned_artifact
from ..core import artifact_digest, canonical_bytes, digest, stable_id, write_json
from ..l1_control import (
    BudgetState,
    L1Action,
    L1ControlError,
    L1ControlPolicy,
    ProgressObservation,
    ProgressStatus,
    apply_action_result,
    create_action,
    observe_progress,
    reduce_terminal,
    validate_action_chain,
)
from ..query_planning import QueryPlanningResult, compile_query_plan_candidate
from .builders import (
    build_attempt_zero,
    build_observation_zero,
    build_off_shadow_comparison,
    build_shadow_action,
    build_shadow_terminal,
)
from .constants import (
    ATTEMPT_CONTRACT,
    LANES,
    OBSERVATION_CONTRACT,
    SHADOW_MODES,
    TELEMETRY_KEYS,
    TERMINAL_CONTRACT,
)
from .helpers import (
    _attempt_action,
    _canonical,
    _digest,
    _outcome_map,
    _pair,
    _require_mode,
    _requirements,
    _selected_records,
    _terminal_reason,
)
from .models import ShadowReceiptError, ShadowReceiptSet
from .receipts import (
    build_shadow_receipts,
    shadow_telemetry_payload,
    validate_shadow_telemetry,
    write_shadow_receipts,
)

__all__ = [
    "ATTEMPT_CONTRACT",
    "Any",
    "BudgetState",
    "L1Action",
    "L1ControlError",
    "L1ControlPolicy",
    "LANES",
    "Mapping",
    "OBSERVATION_CONTRACT",
    "Path",
    "ProgressObservation",
    "ProgressStatus",
    "QueryPlanningResult",
    "SHADOW_MODES",
    "Sequence",
    "ShadowReceiptError",
    "ShadowReceiptSet",
    "TELEMETRY_KEYS",
    "TERMINAL_CONTRACT",
    "_attempt_action",
    "_canonical",
    "_digest",
    "_outcome_map",
    "_pair",
    "_require_mode",
    "_requirements",
    "_selected_records",
    "_terminal_reason",
    "apply_action_result",
    "artifact_digest",
    "build_attempt_zero",
    "build_observation_zero",
    "build_off_shadow_comparison",
    "build_shadow_action",
    "build_shadow_receipts",
    "build_shadow_terminal",
    "canonical_bytes",
    "compile_query_plan_candidate",
    "create_action",
    "create_versioned_artifact",
    "dataclass",
    "digest",
    "observe_progress",
    "reduce_terminal",
    "shadow_telemetry_payload",
    "stable_id",
    "validate_action_chain",
    "validate_shadow_telemetry",
    "write_json",
    "write_shadow_receipts",
]
