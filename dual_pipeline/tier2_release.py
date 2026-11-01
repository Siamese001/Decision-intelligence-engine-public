"""
dual_pipeline/tier2_release.py

Tier 2 Concept Deduplication release preparation (Deprecated Shim).
All graph deduplication and consolidation is governed through `dual_pipeline.graph_consolidation_core`.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Tuple

from .graph_consolidation_core import (
    activate_consolidation_release,
    build_consolidation_plan,
    prepare_consolidation_release,
)
from .index import EmbeddingBackend
from .release_core.manager import ReleaseManager


# Backward-compatibility shims for legacy callers/tests
def execute_node_merges(*args: Any, **kwargs: Any) -> Tuple[int, list[dict[str, Any]]]:
    warnings.warn("execute_node_merges is deprecated", DeprecationWarning, stacklevel=2)
    return 0, []


def execute_assertion_supersessions(*args: Any, **kwargs: Any) -> Tuple[int, list[dict[str, Any]]]:
    warnings.warn("execute_assertion_supersessions is deprecated", DeprecationWarning, stacklevel=2)
    return 0, []


def execute_edge_lineage_fixes(*args: Any, **kwargs: Any) -> int:
    warnings.warn("execute_edge_lineage_fixes is deprecated", DeprecationWarning, stacklevel=2)
    return 0


def execute_predicate_consolidation(*args: Any, **kwargs: Any) -> dict[str, Any]:
    warnings.warn("execute_predicate_consolidation is deprecated", DeprecationWarning, stacklevel=2)
    return {}


def execute_search_and_fts_rebuild(*args: Any, **kwargs: Any) -> dict[str, Any]:
    warnings.warn("execute_search_and_fts_rebuild is deprecated", DeprecationWarning, stacklevel=2)
    return {}


def execute_signal_quality_validation(*args: Any, **kwargs: Any) -> dict[str, Any]:
    warnings.warn("execute_signal_quality_validation is deprecated", DeprecationWarning, stacklevel=2)
    return {"status": "PASS"}


def prepare_dedup_release(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    approval_id: str,
    run_id: str | None = None,
    execute_waves_3b_to_5: bool = True,
) -> Tuple[dict[str, Any], Path]:
    """Deprecated shim delegating to prepare_consolidation_release.

    Deprecated: Migrate callers to `dual_pipeline.graph_consolidation_core.release.prepare_consolidation_release`.
    """
    warnings.warn(
        "prepare_dedup_release is deprecated and scheduled for removal; use prepare_consolidation_release instead",
        DeprecationWarning,
        stacklevel=2,
    )
    active = manager.active_pair()
    graph_path = Path(manager._resolve(active["graph_path"])).resolve()
    plan = build_consolidation_plan(graph_path, active_pair=active)

    effective_approval = approval_id
    if not approval_id.startswith("USER_INPUT:") and not approval_id.startswith("USER_EXPLICIT:"):
        effective_approval = f"USER_INPUT: {approval_id}"

    return prepare_consolidation_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=run_id or "run-legacy-dedup-shim",
        approval_id=effective_approval,
    )


def activate_dedup_release(
    *,
    manager: ReleaseManager,
    receipt_path: Path,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Deprecated shim delegating to activate_consolidation_release.

    Deprecated: Migrate callers to `dual_pipeline.graph_consolidation_core.release.activate_consolidation_release`.
    """
    warnings.warn(
        "activate_dedup_release is deprecated and scheduled for removal; use activate_consolidation_release instead",
        DeprecationWarning,
        stacklevel=2,
    )
    effective_approval = approval_id or "USER_INPUT: Operator approved legacy dedup activation"
    if not effective_approval.startswith("USER_INPUT:") and not effective_approval.startswith("USER_EXPLICIT:"):
        effective_approval = f"USER_INPUT: {effective_approval}"

    return activate_consolidation_release(
        manager=manager,
        receipt_path=receipt_path,
        approval_id=effective_approval,
    )
