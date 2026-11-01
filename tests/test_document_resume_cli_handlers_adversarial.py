"""Wave 2 Adversarial & Edge-Case Tests: CLI handlers auth/rollback, judge verdict consistency, and resume bindings."""
from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.canonical_pipeline.document_resume import (
    FreshDocumentStages,
    lock_document_source,
    open_stage_checkpoint,
)
from dual_pipeline.cli import engine, handlers
from dual_pipeline.document_pipeline_core.evaluator import _evaluate_output3_judge


def test_remediation_handler_requires_auth_and_rolls_back_on_complete_failure(tmp_path: Path) -> None:
    """CAT1-1000-P0-4 & CAT1-1000-P1-3: _activate_and_complete_remediation enforces auth and rolls back if guard.complete fails."""
    args = argparse.Namespace(
        auth_token=None,
        auth_public_key=None,
        auth_issuer=None,
        auth_audience=None,
        directive_id="dir_123",
    )
    manager = MagicMock()
    guard = MagicMock()
    receipt_path = tmp_path / "release-gen1.json"

    # 1. Production-qualified receipt without auth token fails closed before manager.activate()
    prod_receipt: dict[str, Any] = {
        "receipt_id": "rec_1",
        "release_kind": "candidate_cleanup",
        "previous_pair": {"generation_id": "gen0", "graph_sha256": "a" * 64, "sidecar_sha256": "b" * 64},
        "next_pair": {
            "generation_id": "gen1",
            "graph_sha256": "c" * 64,
            "sidecar_sha256": "d" * 64,
            "production_qualified": True,
            "model_manifest_digest": "e" * 64,
            "embedding_backend": "deterministic-hash-v1",
        },
    }
    with patch("dual_pipeline.cli.handlers.release_action_resources", return_value={}):
        with pytest.raises(PermissionError, match="signed execution requires"):
            handlers._activate_and_complete_remediation(args, manager, guard, prod_receipt, receipt_path)
    manager.activate.assert_not_called()

    # 2. Non-production receipt where guard.complete() raises triggers automatic manager.rollback()
    dev_receipt = {
        **prod_receipt,
        "next_pair": {**prod_receipt["next_pair"], "production_qualified": False},
    }
    guard.complete.side_effect = RuntimeError("disk full during directive seal")
    with patch("dual_pipeline.cli.handlers.release_action_resources", return_value={}):
        with pytest.raises(RuntimeError, match="automatic rollback was executed"):
            handlers._activate_and_complete_remediation(args, manager, guard, dev_receipt, receipt_path)
    manager.activate.assert_called_once()
    manager.rollback.assert_called_once_with(receipt_path, authorization_context=None)


def test_intake_release_activate_rolls_back_when_guard_complete_fails(tmp_path: Path) -> None:
    """CAT1-1000-P1-3: _handle_intake_release_activate rolls back active pair if guard.complete raises."""
    receipt_path = tmp_path / "release-gen1.json"
    receipt = {
        "receipt_id": "rec_1",
        "next_pair": {
            "generation_id": "gen1",
            "production_qualified": False,
            "embedding_backend": "deterministic-hash-v1",
        },
    }
    args = argparse.Namespace(
        receipt=receipt_path,
        directive_id="dir_123",
        runtime_root=tmp_path,
        graph=tmp_path / "graph.sqlite",
        auth_token=None,
        auth_public_key=None,
        auth_issuer=None,
        auth_audience=None,
        worktree_dir=None,
    )
    mock_mgr = MagicMock()
    mock_guard = MagicMock()
    mock_guard.complete.side_effect = RuntimeError("ledger write error")

    with (
        patch("dual_pipeline.cli.engine.read_json", return_value=receipt),
        patch("dual_pipeline.release.release_action_resources", return_value={}),
        patch("dual_pipeline.release.ReleaseManager", return_value=mock_mgr),
        patch("dual_pipeline.cli.engine._completion_guard", return_value=mock_guard),
    ):
        with pytest.raises(RuntimeError, match="automatic rollback was executed"):
            engine._handle_intake_release_activate(args)
    mock_mgr.activate.assert_called_once()
    mock_mgr.rollback.assert_called_once()


def test_evaluate_output3_judge_forces_fail_verdict_on_final_attempt_disqualification() -> None:
    """CAT1-1000-P1-1: Disqualified document judge forces verdict='FAIL' even on final attempt or missing directive."""
    contradictory_eval = {
        "status": "FAIL",
        "verdict": "PASS",
        "remediation_directive": "",
        "composite_score": 62,
    }
    with patch(
        "dual_pipeline.document_pipeline_core.evaluator._execute_document_judge",
        return_value=(contradictory_eval, False, 62),
    ):
        j3_eval, j3_pass, _ = _evaluate_output3_judge(
            q3_pass=True,
            qa_report={},
            meta={},
            eff_model="gpt-test",
            eff_effort="high",
            reasoning_effort="high",
            fast_judge=False,
            f3_str="{}",
            source_text="source",
            mock=True,
            attempt=2,
            max_attempts=2,
        )
    assert j3_pass is False
    assert j3_eval["verdict"] == "FAIL"


def test_lock_document_source_normalizes_null_project_and_stage_cache_defaults_true(tmp_path: Path) -> None:
    """CAT1-1000-P1-2: lock_document_source normalizes None/auto project and open_stage_checkpoint enables stage cache."""
    meta_none: dict[str, Any] = {"is_document": True, "project": None}
    meta_auto: dict[str, Any] = {"is_document": True, "project": "auto"}
    meta_explicit: dict[str, Any] = {"is_document": True, "project": "internal_consulting"}

    with contextlib.ExitStack() as stack:
        lock_document_source(stack, meta_none, "doc body", tmp_path / "rt1")
    with contextlib.ExitStack() as stack:
        lock_document_source(stack, meta_auto, "doc body", tmp_path / "rt2")
    with contextlib.ExitStack() as stack:
        lock_document_source(stack, meta_explicit, "doc body", tmp_path / "rt3")

    assert Path(meta_none["_document_checkpoint_path"]).name == Path(meta_explicit["_document_checkpoint_path"]).name
    assert Path(meta_auto["_document_checkpoint_path"]).name == Path(meta_explicit["_document_checkpoint_path"]).name

    stage_ckpt = open_stage_checkpoint(tmp_path / "ckpt.json", {"is_document": True})
    assert isinstance(stage_ckpt, FreshDocumentStages)
    assert stage_ckpt.enable_stage_cache is True
    stage_ckpt.save_stage("L1", {"ok": True})
    assert stage_ckpt.get_stage("L1") == {"ok": True}


def test_engine_execute_fails_closed_on_invalid_max_cost_usd() -> None:
    """CAT1-1000-P1-4: Invalid or non-positive --max-cost-usd raises ValueError instead of swallowing."""
    args = argparse.Namespace(command="active-pair", max_cost_usd=-5.0, worktree_dir=None)
    with pytest.raises(ValueError, match="--max-cost-usd must be > 0"):
        engine.execute(args)
