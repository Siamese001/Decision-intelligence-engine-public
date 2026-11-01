"""tests/end_to_end/test_task_restoration_wave_e2e.py

End-to-end lifecycle verification test for Antigravity task restoration waves.
Tests:
1. Full Wave 1 verification runner execution, receipt emission, and lock acquisition.
2. Parent receipt verification and chaining for Wave 2.
3. Non-overwritable receipt tampering prevention.
4. Complete live inventory validation across all seven reconciled tasks.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from tools.task_restoration_core import (
    compute_canonical_digest,
    verify_requirements_inventory,
)
from tools.verify_task_restoration_wave import (
    verify_parent_receipt,
    verify_wave,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def temp_output_root() -> Generator[Path, None, None]:
    tmp = Path(tempfile.mkdtemp(prefix="task_restoration_e2e_"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def test_all_seven_tasks_inventory_e2e() -> None:
    """Verifies that the canonical requirements inventory satisfies all repository invariants."""
    req_file = ROOT / "docs" / "plans" / "antigravity_task_requirements.json"
    ok, errors = verify_requirements_inventory(req_file, ROOT)
    assert ok is True, f"Inventory verification failed with errors: {errors}"
    assert len(errors) == 0


def test_wave1_end_to_end_verification(temp_output_root: Path) -> None:
    """Runs Wave 1 verification runner end-to-end and validates sealed receipt and lock."""
    run_id = "test-e2e-wave1-run"
    retcode = verify_wave(
        wave=1,
        run_id=run_id,
        output_root=temp_output_root,
        parent_receipt=None,
        repo_root=ROOT,
        test_files_override=["tests/test_verify_task_restoration_wave_adversarial.py"],
    )
    assert retcode == 0, "Wave 1 verification must succeed"

    # Verify sealed receipt
    receipt_file = temp_output_root / "wave_1_receipt.json"
    assert receipt_file.is_file(), "Sealed receipt must be written"

    data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert data["contract_id"] == "urn:nee:antigravity-task-restoration-receipt:1.0.0"
    assert data["receipt_type"] == "antigravity_task_restoration_receipt"
    assert data["wave"] == 1
    assert data["run_id"] == run_id
    assert data["terminal_outcome"] == "PASS"
    assert data["parent_receipt_digest"] is None
    assert data["tests_passed"] > 0
    assert data["tests_failed"] == 0
    assert data["tests_skipped"] == 0

    # Verify canonical digest
    digest = data.get("canonical_digest")
    assert digest is not None
    assert compute_canonical_digest(data) == digest

    # Verify operational accounting
    oa = data.get("operational_accounting", {})
    assert oa.get("verified_clean") is True
    assert oa.get("live_graph_mutations") == 0

    # Verify sequential lock file
    lock_file = ROOT / "artifacts" / "locks" / "antigravity_task_restoration_wave_1.complete"
    assert lock_file.is_file(), "Sequential completion lock must be acquired"
    lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
    assert lock_data["wave"] == 1
    assert lock_data["run_id"] == run_id
    assert lock_data["canonical_digest"] == digest


def test_wave2_parent_receipt_chaining(temp_output_root: Path) -> None:
    """Verifies that Wave 2 correctly chains and validates the sealed Wave 1 receipt."""
    receipt_file = temp_output_root / "wave_1_receipt.json"
    if not receipt_file.is_file():
        # Generate valid wave 1 receipt if not already present
        verify_wave(
            wave=1,
            run_id="test-e2e-chaining",
            output_root=temp_output_root,
            parent_receipt=None,
            repo_root=ROOT,
            test_files_override=["tests/test_verify_task_restoration_wave_adversarial.py"],
        )

    parent_digest = verify_parent_receipt(wave=2, parent_receipt=receipt_file)
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert parent_digest == receipt_data["canonical_digest"]


def test_non_overwritable_receipt_defense(temp_output_root: Path) -> None:
    """Attempting to overwrite a sealed receipt with different contents fails closed."""
    receipt_file = temp_output_root / "wave_1_receipt.json"
    receipt_file.write_text('{"tampered": true}', encoding="utf-8")

    retcode = verify_wave(
        wave=1,
        run_id="test-overwrite-attack",
        output_root=temp_output_root,
        parent_receipt=None,
        repo_root=ROOT,
        test_files_override=["tests/test_verify_task_restoration_wave_adversarial.py"],
    )
    assert retcode == 1, "Attempting to overwrite sealed receipt must fail closed"
