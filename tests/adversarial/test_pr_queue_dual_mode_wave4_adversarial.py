"""Wave 4 adversarial process-lock and failed-baseline preservation checks."""
from __future__ import annotations

import json
import multiprocessing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.pr_governance_core.observer import ObserverLockCollisionError, acquire_observer_lock, run_observation
from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot


def _hold_lock(path: str, ready, release) -> None:
    with acquire_observer_lock(Path(path)):
        ready.set()
        release.wait(10)


def test_independent_processes_cannot_hold_the_same_lock(tmp_path: Path):
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    worker = multiprocessing.Process(target=_hold_lock, args=(str(tmp_path / "observer.lock"), ready, release))
    worker.start()
    assert ready.wait(5)
    try:
        with pytest.raises(ObserverLockCollisionError):
            with acquire_observer_lock(tmp_path / "observer.lock"):
                pass
    finally:
        release.set()
        worker.join(5)
    assert worker.exitcode == 0
    with acquire_observer_lock(tmp_path / "observer.lock"):
        pass


def test_partial_collection_preserves_scheduled_baseline_and_separates_failure(tmp_path: Path):
    state = tmp_path / "state"
    good = QueueSnapshot(observed_at=datetime.now(timezone.utc).isoformat(), branches=[BranchSnapshot("feat/a", "head", ahead=1, behind=0, clean_merge=True)])
    run_observation(tmp_path, state, snapshot=good, mode="scheduled")
    baseline = (state / "scheduled_baseline.json").read_bytes()
    failed = QueueSnapshot(observed_at=datetime.now(timezone.utc).isoformat(), errors=["fixture collection failed"], completeness_status="INCONSISTENT")
    receipt = run_observation(tmp_path, state, snapshot=failed, mode="scheduled")
    assert receipt.observation_outcome == "BLOCKED"
    assert (state / "scheduled_baseline.json").read_bytes() == baseline
    assert json.loads((state / "scheduled_failure_history.json").read_text(encoding="utf-8"))["failure_signature"]


def test_interactive_failure_cannot_suppress_first_scheduled_failure(tmp_path: Path):
    state = tmp_path / "state"
    failed = QueueSnapshot(observed_at=datetime.now(timezone.utc).isoformat(), errors=["same fixture failure"], completeness_status="INCONSISTENT")
    interactive = run_observation(tmp_path, state, snapshot=failed, mode="interactive")
    assert interactive.assessment.notify is False
    assert (state / "interactive_failure_history.json").is_file()
    assert not (state / "scheduled_failure_history.json").exists()
    scheduled = run_observation(tmp_path, state, snapshot=failed, mode="scheduled")
    assert scheduled.assessment.terminal_outcome == "COLLECTION_FAILURE_ALERT"
    assert scheduled.assessment.notify is True
