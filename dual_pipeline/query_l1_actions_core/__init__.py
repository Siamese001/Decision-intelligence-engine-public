"""Core subpackage for isolated, bounded query L1 retrieval actions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import create_versioned_artifact
from ..core import (
    artifact_digest,
    canonical_bytes,
    digest,
    seal_artifact,
    sha256_file,
    write_json,
)
from ..index import HybridRetriever, validate_sidecar
from ..l1_control import (
    ACTION_TYPES,
    LANES,
    BudgetState,
    L1Action,
    L1ControlError,
    L1ControlPolicy,
    apply_action_result,
    guard_action,
)
from ..query_planning import CompiledRequirementQuery, compile_requirement_query
from .constants import ATTEMPT_CONTRACT, CARD_SCOPE_EXTENSION, LANE_ORDER
from .execution import (
    ACTION_REGISTRY,
    _execute_retrieval,
    _execute_stop,
    execute_l1_action,
    write_controlled_retrieval_artifact,
)
from .helpers import (
    _attempt_artifact,
    _backend_snapshot,
    _blocked,
    _digest,
    _normalize_lanes,
    _pair,
    _public_diagnostics,
    _query_bindings,
    _requirements,
    _scope_bindings,
    _sealed,
)
from .models import ActionExecutionResult, ActionLimits, L1ActionExecutionError

__all__ = [
    "ACTION_REGISTRY",
    "ACTION_TYPES",
    "ATTEMPT_CONTRACT",
    "ActionExecutionResult",
    "ActionLimits",
    "Any",
    "BudgetState",
    "CARD_SCOPE_EXTENSION",
    "CompiledRequirementQuery",
    "HybridRetriever",
    "Iterable",
    "L1Action",
    "L1ActionExecutionError",
    "L1ControlError",
    "L1ControlPolicy",
    "LANES",
    "LANE_ORDER",
    "Mapping",
    "MappingProxyType",
    "Path",
    "Sequence",
    "_attempt_artifact",
    "_backend_snapshot",
    "_blocked",
    "_digest",
    "_execute_retrieval",
    "_execute_stop",
    "_normalize_lanes",
    "_pair",
    "_public_diagnostics",
    "_query_bindings",
    "_requirements",
    "_scope_bindings",
    "_sealed",
    "apply_action_result",
    "artifact_digest",
    "canonical_bytes",
    "compile_requirement_query",
    "create_versioned_artifact",
    "dataclass",
    "digest",
    "execute_l1_action",
    "guard_action",
    "perf_counter",
    "replace",
    "seal_artifact",
    "sha256_file",
    "validate_sidecar",
    "write_controlled_retrieval_artifact",
    "write_json",
]
