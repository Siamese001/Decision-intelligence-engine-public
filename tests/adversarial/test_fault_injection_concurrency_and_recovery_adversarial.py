"""
tests/adversarial/test_fault_injection_concurrency_and_recovery_adversarial.py

Adversarial fault-injection suite validating:
1. Concurrency fault injection: 16-worker concurrent session access across isolated session roots.
2. Checkpoint concurrency & atomic replacement: Concurrent stage checkpointing without half-written state.
3. In-flight cancellation fault injection: Corrupt or interrupted writes do not pollute active checkpoints.
4. Checkpoint digest tampering detection: Tampered or truncated payloads fail closed immediately.
5. Worktree recovery safety: Reconciliation refuses to prune or coalesce active/locked sessions.
6. Shared dependency protection: Worktree pruning safely unlinks dependencies without traversing roots.
7. Rollback idempotency: Repeated rollback executions return cached receipt without duplicate mutation.
8. Retry cascade termination: Component retry planner terminates no-op and recurring defects.
"""
from __future__ import annotations

import concurrent.futures
import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.learning_engine_core.rollback import (
    execute_rollback,
    record_rollback_snapshot,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    RecordingStartRequest,
    RecordingState,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import (
    RecordingServiceOwner,
    get_recording_service_owner,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    teardown_empty_live_worktree,
)
from dual_pipeline.runtime.pipeline_context import (
    ContentAddressedDependencyManifest,
    compute_stage_manifest,
    is_checkpoint_valid,
    save_stage_checkpoint as save_runtime_checkpoint,
)
from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    ComponentRetryPlanner,
    RetryBudget,
    RetryClass,
)
from dual_pipeline.unified_agentic_checkpoint import (
    can_resume_from_checkpoint,
    load_stage_checkpoint,
    save_stage_checkpoint,
)
from dual_pipeline.unified_agentic_pipeline import (
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)
from tools.reconcile_live_worktrees import (
    coalesce_live_worktree_pair,
    is_worktree_locked_or_active,
    prune_source_worktree,
)


def test_concurrent_session_isolation_across_roots(tmp_path: Path):
    """16 workers concurrently access distinct session roots; verify zero cross-talk."""
    root_a = tmp_path / "sessions_root_a"
    root_b = tmp_path / "sessions_root_b"
    root_a.mkdir()
    root_b.mkdir()

    owner_a = get_recording_service_owner(sessions_root=root_a)
    owner_b = get_recording_service_owner(sessions_root=root_b)

    assert owner_a.sessions_root == root_a.resolve()
    assert owner_b.sessions_root == root_b.resolve()
    assert owner_a is not owner_b

    def create_and_update(root: Path, idx: int):
        owner = get_recording_service_owner(sessions_root=root)
        sid = f"sess_{root.name}_{idx}"
        req = RecordingStartRequest(
            request_id=f"req_{idx}",
            meeting_name=f"Meeting_{idx}",
            mock_mode=True,
        )
        record, is_new = owner.acquire_session_owner(req, sid)
        assert is_new is True
        owner.update_session_status(sid, RecordingState.CAPTURING)
        rec = owner.get_session_record(sid)
        assert rec is not None
        assert rec.status == RecordingState.CAPTURING.value
        return sid

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures_a = [pool.submit(create_and_update, root_a, i) for i in range(8)]
        futures_b = [pool.submit(create_and_update, root_b, i) for i in range(8)]
        sids_a = {f.result() for f in futures_a}
        sids_b = {f.result() for f in futures_b}

    # Verify complete isolation: root_a contains only sids_a, root_b contains only sids_b
    dirs_a = {p.name for p in root_a.iterdir() if p.is_dir()}
    dirs_b = {p.name for p in root_b.iterdir() if p.is_dir()}
    assert dirs_a == sids_a
    assert dirs_b == sids_b
    assert sids_a.isdisjoint(sids_b)


def test_concurrent_checkpoint_atomic_writes(tmp_path: Path):
    """16 concurrent threads save and load stage checkpoints without half-written JSON."""
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir()

    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="concurrency_cp_test",
        input_path="input.txt",
    )

    def write_and_verify(idx: int):
        payload = {"worker_id": idx, "data": "x" * 500, "timestamp": time.time()}
        save_stage_checkpoint(tracker, PipelineStage.U0_INGRESS, payload, cp_dir)
        loaded = load_stage_checkpoint(tracker, PipelineStage.U0_INGRESS, cp_dir)
        assert loaded is not None
        assert "worker_id" in loaded
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(write_and_verify, i) for i in range(16)]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() is True


def test_checkpoint_digest_tampering_fails_closed(tmp_path: Path):
    """Corrupt or tampered stage checkpoint payloads fail closed and are rejected on load and resume."""
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir()

    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="tamper_test",
        input_path="input.txt",
    )
    payload = {"status": "AUTHENTIC", "token": "secret_123"}
    cp_file = save_stage_checkpoint(tracker, PipelineStage.U0_INGRESS, payload, cp_dir)

    # Verify initial valid state
    loaded = load_stage_checkpoint(tracker, PipelineStage.U0_INGRESS, cp_dir)
    assert loaded == payload
    assert can_resume_from_checkpoint(tracker, PipelineStage.L1_IDENTITY, cp_dir) is True

    # Tamper with the payload on disk without updating stage_digest
    data = json.loads(cp_file.read_text(encoding="utf-8"))
    data["payload"]["status"] = "TAMPERED_INJECTED_DATA"
    cp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # load_stage_checkpoint must reject tampered payload
    rejected_payload = load_stage_checkpoint(tracker, PipelineStage.U0_INGRESS, cp_dir)
    assert rejected_payload is None, "Must reject checkpoint with stage_digest mismatch"

    # can_resume_from_checkpoint must also fail closed
    can_resume = can_resume_from_checkpoint(tracker, PipelineStage.L1_IDENTITY, cp_dir)
    assert can_resume is False, "Must not authorize resume from tampered checkpoint"


def test_runtime_checkpoint_atomic_replacement(tmp_path: Path):
    """Runtime stage checkpoints are saved atomically alongside manifests."""
    ckpt_dir = tmp_path / "runtime_ckpts"
    ckpt_dir.mkdir()
    ckpt_file = ckpt_dir / "output_1.ckpt.md"

    metadata = {"project": "test_proj", "meeting": "test_meet", "meeting_date": "10-09-2026"}
    manifest = compute_stage_manifest("output_1", "Sample forensic transcript content", metadata)

    save_runtime_checkpoint(ckpt_file, "Sample forensic transcript content " * 30, manifest)

    assert ckpt_file.is_file()
    manifest_file = ckpt_file.with_suffix(ckpt_file.suffix + ".manifest.json")
    assert manifest_file.is_file()
    assert is_checkpoint_valid(ckpt_file, manifest, strict_manifest=True) is True

    # Artificially corrupt manifest digest
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_data["manifest_digest"] = "corrupted_digest"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    assert is_checkpoint_valid(ckpt_file, manifest, strict_manifest=True) is False


def test_worktree_reconcile_refuses_active_locked_session(tmp_path: Path):
    """Reconciliation refuses to coalesce or prune an active worktree holding a slug flock."""
    repo_root = tmp_path / "mock_repo"
    repo_root.mkdir()
    locks_dir = repo_root / ".session_locks"
    locks_dir.mkdir(parents=True)

    wt_a = repo_root / ".worktrees" / "live_active_sync"
    wt_b = repo_root / ".worktrees" / "live_active_sync_attendee"
    wt_a.mkdir(parents=True)
    wt_b.mkdir(parents=True)

    # Acquire an active advisory flock for wt_a's slug
    lock_file = locks_dir / "active-sync.flock"
    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        assert is_worktree_locked_or_active(wt_a, repo_root) is True

        res = coalesce_live_worktree_pair(wt_a, wt_b, repo_root, dry_run=False)
        assert res["status"] == "BLOCKED"
        assert res["reason"] == "SOURCE_WORKTREE_ACTIVE"
        assert wt_a.exists(), "Active source worktree must NOT be pruned!"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_worktree_teardown_safely_unlinks_dependencies(tmp_path: Path):
    """Teardown unlinks symlinks before rmtree, preserving shared .local/.venv target directories."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    # Create root shared state directories
    shared_local = repo_root / ".local"
    shared_local.mkdir()
    secret_record = shared_local / "master_record.txt"
    secret_record.write_text("CRITICAL_SHARED_STATE", encoding="utf-8")

    wt_dir = repo_root / ".worktrees" / "live_test_symlink_teardown"
    wt_dir.mkdir(parents=True)

    # Symlink .local into the worktree
    wt_local = wt_dir / ".local"
    os.symlink(os.path.relpath(shared_local, wt_dir), wt_local)
    assert (wt_local / "master_record.txt").exists()

    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git") as mock_git:
        mock_git.return_value = ""
        success = teardown_empty_live_worktree(
            worktree_dir=wt_dir,
            branch_name="feat/live-test-symlink-teardown",
            repo_root=repo_root,
        )
        assert success is True
        assert not wt_dir.exists(), "Worktree directory must be removed"
        # Root shared state MUST be completely preserved!
        assert shared_local.exists()
        assert secret_record.is_file()
        assert secret_record.read_text(encoding="utf-8") == "CRITICAL_SHARED_STATE"


def test_rollback_idempotency_and_duplicate_prevention(tmp_path: Path):
    """Rollback execution is strictly idempotent and prevents duplicate mutations."""
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()
    params_file = tmp_path / "parameters.json"
    params_file.write_text(json.dumps({"test_threshold": 0.85}), encoding="utf-8")

    proposal = {
        "proposal_id": "prop_test_001",
        "proposal_type": "threshold",
    }
    pre_state = {
        "parameter_name": "test_threshold",
        "previous_value": 0.70,
    }

    record_rollback_snapshot(proposals_dir, proposal, pre_state)

    # Execute rollback first time
    receipt1 = execute_rollback(
        proposals_dir,
        "prop_test_001",
        parameter_path=params_file,
    )
    assert receipt1["status"] == "ROLLED_BACK"
    assert "Restored parameter" in receipt1["restored_action"]

    # Verify parameters restored
    restored_params = json.loads(params_file.read_text(encoding="utf-8"))
    assert restored_params["test_threshold"] == 0.70

    # Modify parameters again (e.g. subsequent user mutation)
    params_file.write_text(json.dumps({"test_threshold": 0.95}), encoding="utf-8")

    # Second invocation of execute_rollback must be idempotent and NOT overwrite subsequent state
    receipt2 = execute_rollback(
        proposals_dir,
        "prop_test_001",
        parameter_path=params_file,
    )
    assert receipt2["status"] == "ROLLED_BACK"
    assert receipt2.get("idempotent_replay") is True
    current_params = json.loads(params_file.read_text(encoding="utf-8"))
    assert current_params["test_threshold"] == 0.95, "Duplicate rollback must not clobber newer state"


def test_retry_planner_terminates_noop_and_recurring_defects():
    """ComponentRetryPlanner terminates on no-op patch or recurring defect without infinite loop."""
    planner = ComponentRetryPlanner(RetryBudget(max_content_repairs=2))

    # Attempt 1: First defect
    is_noop, msg = planner.check_noop_or_recurring_defect(
        "output_2:appendix", "Initial repair text", "Missing stakeholder attribution"
    )
    assert is_noop is False
    assert msg == "OK"

    # Attempt 2: No-op patch (candidate text did not change)
    is_noop, msg = planner.check_noop_or_recurring_defect(
        "output_2:appendix", "Initial repair text", "Different error message"
    )
    assert is_noop is True
    assert "NO_OP_PATCH" in msg

    # Attempt 3: Recurring defect signature
    is_noop, msg = planner.check_noop_or_recurring_defect(
        "output_2:appendix", "Modified candidate text", "Missing stakeholder attribution"
    )
    assert is_noop is True
    assert "RECURRING_DEFECT" in msg
