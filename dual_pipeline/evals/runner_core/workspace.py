"""Workspace lifecycle, pruning, bootstrapping, and auditing for evaluation runs."""
from __future__ import annotations

import shutil
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from ...core import (
    assert_safe_mutable_path,
    read_json,
    utc_now,
    write_json_exclusive,
)
from ...index import EmbeddingBackend
from ...release import ReleaseManager
from ...telemetry import RunRecorder
from .constants import (
    FAILED_WORKSPACE_RETENTION_DAYS,
    WORKSPACE_RETENTION_FILE,
    EvaluationFailure,
    _format_utc,
    _parse_utc,
)


def _dispatch(name: str, fallback: Any) -> Any:
    mod = sys.modules.get("dual_pipeline.evals.runner")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return fallback


def _remove_workspace(work_parent: Path, workspace: Path) -> None:
    parent = work_parent.expanduser().resolve()
    target = workspace.expanduser().absolute()
    if target.is_symlink():
        raise EvaluationFailure(f"refusing to remove symlinked evaluation workspace: {target}")
    target = target.resolve()
    assert_safe_mutable_path(parent, target, label="evaluation workspace cleanup path")
    if target.parent != parent:
        raise EvaluationFailure(f"evaluation workspace is not a direct child of {parent}: {target}")
    shutil.rmtree(target)
    if work_parent.is_dir() and not any(work_parent.iterdir()):
        work_parent.rmdir()


def _evaluation_workspace_lifecycle(
    work_root: Path,
    *,
    status: str,
    evaluated_at: str,
) -> dict[str, Any]:
    if status == "PASS":
        return {
            "policy": "DELETE_PASS_RETAIN_FAILURE_7_DAYS",
            "status": "DELETED_ON_PASS",
            "workspace_path": None,
            "expires_at": None,
        }
    expires_at = _format_utc(
        _parse_utc(evaluated_at) + timedelta(days=FAILED_WORKSPACE_RETENTION_DAYS)
    )
    return {
        "policy": "DELETE_PASS_RETAIN_FAILURE_7_DAYS",
        "status": "RETAINED_FAILURE",
        "workspace_path": str(work_root.resolve()),
        "expires_at": expires_at,
    }


def _finalize_evaluation_workspace(
    work_root: Path,
    *,
    evaluation_id: str,
    status: str,
    evaluated_at: str,
) -> dict[str, Any]:
    lifecycle = _evaluation_workspace_lifecycle(
        work_root,
        status=status,
        evaluated_at=evaluated_at,
    )
    if status == "PASS":
        _remove_workspace(work_root.parent, work_root)
        return lifecycle
    manifest = {
        "schema_version": "1.0.0",
        "evaluation_id": evaluation_id,
        "evaluation_status": status,
        "retention_policy": lifecycle["policy"],
        "workspace_path": lifecycle["workspace_path"],
        "retained_at": evaluated_at,
        "expires_at": lifecycle["expires_at"],
    }
    write_json_exclusive(work_root / WORKSPACE_RETENTION_FILE, manifest)
    return lifecycle


def prune_expired_failed_workspaces(
    runtime_root: Path,
    *,
    now: str | None = None,
) -> list[str]:
    """Delete only expired failed-evaluation workspaces with valid local manifests."""

    root = Path(runtime_root).expanduser().absolute().resolve()
    work_parent = root / ".eval-work"
    if not work_parent.is_dir():
        return []
    current = _parse_utc(now or utc_now())
    removed: list[str] = []
    for workspace in sorted(work_parent.iterdir()):
        if workspace.is_symlink() or not workspace.is_dir():
            continue
        manifest_path = workspace / WORKSPACE_RETENTION_FILE
        if not manifest_path.is_file():
            continue
        try:
            manifest = read_json(manifest_path)
            valid = (
                manifest.get("schema_version") == "1.0.0"
                and manifest.get("evaluation_status") == "FAIL"
                and manifest.get("retention_policy")
                == "DELETE_PASS_RETAIN_FAILURE_7_DAYS"
                and manifest.get("workspace_path") == str(workspace.resolve())
                and isinstance(manifest.get("expires_at"), str)
            )
            if not valid or _parse_utc(manifest["expires_at"]) > current:
                continue
        except (OSError, TypeError, ValueError):
            continue
        _remove_workspace(work_parent, workspace)
        removed.append(workspace.name)
    return removed


def _bootstrap(
    case_runtime: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    *,
    template_runtime: Path | None = None,
) -> tuple[ReleaseManager, EmbeddingBackend, Path, Path]:
    if template_runtime is not None:
        if case_runtime.exists():
            raise EvaluationFailure(f"evaluation case runtime already exists: {case_runtime}")
        if not template_runtime.is_dir():
            raise EvaluationFailure(
                f"verified BGE evaluation template is missing: {template_runtime}"
            )
        # The template is built by the real BGE-M3 encoder once per suite.
        # Each case receives an independent copy before it can write runs,
        # cache entries, receipts, or a changed graph generation.
        shutil.copytree(template_runtime, case_runtime)
    manager = _dispatch("ReleaseManager", ReleaseManager)(case_runtime, seed_graph)
    pair = manager.active_pair() if template_runtime is not None else manager.bootstrap(backend)
    return (
        manager,
        backend,
        manager._resolve(pair["graph_path"]),
        manager._resolve(pair["sidecar_path"]),
    )


def _audit(
    runtime_root: Path,
    *,
    run_id: str,
    trace_id: str,
    use_case: str,
    graph_snapshot: str | None,
    generation_id: str | None,
    required_stages: Iterable[str],
) -> dict[str, Any]:
    recorder = RunRecorder(
        runtime_root,
        run_id=run_id,
        trace_id=trace_id,
        use_case=use_case,
        graph_snapshot=graph_snapshot,
        model_or_index_generation=generation_id,
    )
    return recorder.audit(required_stages)
