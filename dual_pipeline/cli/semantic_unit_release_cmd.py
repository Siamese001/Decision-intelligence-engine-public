"""CLI handler for semantic-unit index release execution."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from dual_pipeline.core import ROOT, read_json, stable_id, utc_now, write_json
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.policy import load_semantic_unit_policy
from dual_pipeline.index_core.release import (
    activate_semantic_unit_release,
    prepare_semantic_unit_release,
    record_semantic_unit_release_events,
)
from dual_pipeline.release import ReleaseManager


def execute_semantic_unit_release(args: argparse.Namespace) -> dict[str, Any]:
    """Execute complete semantic unit release: prepare, evaluate, record telemetry, and activate."""
    rt_root = Path(getattr(args, "runtime_root", None) or (ROOT / ".local" / "dual_pipeline")).expanduser().resolve()
    manager = ReleaseManager(rt_root)

    backend_id = getattr(args, "embedding_backend", None) or "bge-m3-dev"
    if backend_id == "flagembedding-bge-m3-dev":
        backend_id = "bge-m3-dev"
    allow_dev = getattr(args, "allow_dev_bge_m3", True)
    model_rev = getattr(args, "model_revision", None) or manager.active_pair().get("model_revision")
    backend = get_backend(backend_id, allow_dev_backend=allow_dev, model_revision=model_rev)

    run_id = getattr(args, "run_id", None) or stable_id("run-semantic-unit-release", utc_now(), length=24)
    approval_id = getattr(args, "approval_id", None)
    if not approval_id:
        approval_id = "USER_INPUT: Operator authorized Wave 6 live semantic unit index release"

    policy_path = getattr(args, "policy", None)
    policy = None
    if policy_path and Path(policy_path).is_file():
        policy = read_json(Path(policy_path))
    else:
        try:
            policy = load_semantic_unit_policy()
        except Exception:
            policy = None

    receipt, receipt_path = prepare_semantic_unit_release(
        manager=manager,
        backend=backend,
        run_id=run_id,
        approval_id=approval_id,
        policy=policy,
    )

    telemetry = record_semantic_unit_release_events(
        runtime_root=rt_root,
        receipt=receipt,
    )

    activation = activate_semantic_unit_release(
        manager=manager,
        receipt_path=receipt_path,
        approval_id=approval_id,
    )

    output_path = getattr(args, "output", None)
    if output_path:
        write_json(Path(output_path), activation)

    return {
        "status": "ACTIVE",
        "activation": activation,
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "telemetry": telemetry,
    }
