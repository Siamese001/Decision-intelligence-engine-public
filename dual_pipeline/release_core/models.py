"""State dataclasses for release preparation workflows."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..authorization import AuthorizationContext
from ..index import EmbeddingBackend


@dataclass
class _PrepareManyState:
    run_ids: list[str]
    approval_id: str
    backend: EmbeddingBackend
    fail_after_operations: int | None = None
    authorization_context: AuthorizationContext | None = None
    staged_vector_paths: list[Path] = field(default_factory=list)

    runs_root: Path = field(default_factory=Path)
    components: list[dict[str, Any]] = field(default_factory=list)
    requested_run_ids: list[str] = field(default_factory=list)
    expected_component_bindings: list[dict[str, Any]] = field(default_factory=list)
    existing_matching_receipt: dict[str, Any] | None = None
    active: dict[str, Any] = field(default_factory=dict)
    approved_components: list[dict[str, Any]] = field(default_factory=list)
    actionable_components: list[dict[str, Any]] = field(default_factory=list)
    approved: dict[str, Any] = field(default_factory=dict)
    primary: dict[str, Any] = field(default_factory=dict)
    generation_id: str = ""
    generation_dir: Path = field(default_factory=Path)
    receipt_path: Path = field(default_factory=Path)
    graph_path: Path = field(default_factory=Path)
    sidecar_path: Path = field(default_factory=Path)
    active_graph_path: Path = field(default_factory=Path)
    graph_receipt: dict[str, Any] = field(default_factory=dict)
    label_quality_validation: dict[str, Any] = field(default_factory=dict)
    actual_graph_delta: dict[str, Any] = field(default_factory=dict)
    graph_hash: str = ""
    graph_snapshot: str = ""
    index_receipt: dict[str, Any] = field(default_factory=dict)
    graph_signal_evaluation: dict[str, Any] = field(default_factory=dict)
    next_pair: dict[str, Any] = field(default_factory=dict)
    pair_validation: dict[str, Any] = field(default_factory=dict)
    component_runs: list[dict[str, Any]] = field(default_factory=list)
    receipt: dict[str, Any] = field(default_factory=dict)
