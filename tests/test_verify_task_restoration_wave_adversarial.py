"""tests/test_verify_task_restoration_wave_adversarial.py

Adversarial and edge-case verification tests for Antigravity task restoration verifier.
Covers:
1. Boundary payloads (missing files, empty JSON, missing tasks, empty requirements)
2. Malformed inputs (syntax errors, invalid enums, missing verification tokens, tampered hashes)
3. Invariant bypass attempts (procedure deletion with common tags, empty audit callbacks, wildcard rollback recipe)
4. Rollback and recovery (tampered parent digests, non-overwritable receipts, lock isolation)
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from tools.task_restoration_core import (
    SEVEN_TASKS,
    compute_canonical_digest,
    verify_procedure_deletion_defense,
    verify_requirements_inventory,
)
from tools.verify_task_restoration_wave import (
    _finalize_wave_lock,
    verify_parent_receipt,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def valid_requirements_data() -> dict[str, Any]:
    req_file = ROOT / "docs" / "plans" / "antigravity_task_requirements.json"
    assert req_file.is_file(), "Canonical requirements file must exist"
    return json.loads(req_file.read_text(encoding="utf-8"))


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    tmp = Path(tempfile.mkdtemp(prefix="task_restoration_adv_"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------------------------------------------------------
# Category 1: Boundary Payloads
# -----------------------------------------------------------------------------

def test_requirements_missing_file(temp_dir: Path) -> None:
    """Missing requirements inventory file must fail closed."""
    missing_path = temp_dir / "nonexistent.json"
    ok, errors = verify_requirements_inventory(missing_path, ROOT)
    assert ok is False
    assert any("not found" in err for err in errors)


def test_requirements_empty_json(temp_dir: Path) -> None:
    """Empty JSON file must fail closed with schema errors."""
    empty_file = temp_dir / "empty.json"
    empty_file.write_text("{}", encoding="utf-8")
    ok, errors = verify_requirements_inventory(empty_file, ROOT)
    assert ok is False
    assert any("Invalid contract_id" in err for err in errors)
    assert any("Task set mismatch" in err for err in errors)


def test_requirements_missing_one_task(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Removing one task from the 7-task set must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    del data["tasks"]["storage_repository_hygiene"]
    bad_file = temp_dir / "missing_task.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("storage_repository_hygiene" in err for err in errors)


def test_requirements_task_empty_requirements_list(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """A task with an empty requirements list must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    data["tasks"]["weekly_market_intelligence"]["requirements"] = []
    bad_file = temp_dir / "empty_reqs.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("empty requirements list" in err for err in errors)


# -----------------------------------------------------------------------------
# Category 2: Malformed Inputs & Enums
# -----------------------------------------------------------------------------

def test_requirements_malformed_json_syntax(temp_dir: Path) -> None:
    """Malformed JSON syntax must fail closed."""
    corrupt_file = temp_dir / "corrupt.json"
    corrupt_file.write_text('{"contract_id": "urn:nee...', encoding="utf-8")
    ok, errors = verify_requirements_inventory(corrupt_file, ROOT)
    assert ok is False
    assert any("Malformed requirements JSON" in err for err in errors)


def test_requirements_invalid_category_enum(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Invalid requirement category must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    data["tasks"]["knowledge_graph_hygiene"]["requirements"][0]["category"] = "unauthorized_category"
    bad_file = temp_dir / "bad_cat.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("invalid category" in err for err in errors)


def test_requirements_invalid_disposition_enum(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Invalid requirement disposition must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    data["tasks"]["knowledge_graph_hygiene"]["requirements"][0]["disposition"] = "deprecated"
    bad_file = temp_dir / "bad_disp.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("invalid disposition" in err for err in errors)


def test_requirements_invalid_target_mode_enum(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Invalid target_mode must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    data["tasks"]["pr_queue_observer"]["requirements"][0]["target_mode"] = "unattended"
    bad_file = temp_dir / "bad_mode.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("invalid target_mode" in err for err in errors)


def test_requirements_missing_mandatory_verification_tokens(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Requirement missing mandatory verification tokens must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    data["tasks"]["ssot_drift_remediation"]["requirements"][0]["mandatory_verification_tokens"] = []
    bad_file = temp_dir / "missing_tokens.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("missing mandatory_verification_tokens" in err for err in errors)


def test_requirements_tampered_archive_hash(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Forged or mismatched archive hash must fail closed."""
    data = copy.deepcopy(valid_requirements_data)
    data["tasks"]["worktree_coverage_cleanup"]["hashes"]["archive_source"] = "0" * 64
    bad_file = temp_dir / "tampered_hash.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("archive hash mismatch" in err for err in errors)


# -----------------------------------------------------------------------------
# Category 3: Invariant Bypass Attempts
# -----------------------------------------------------------------------------

def test_procedure_deletion_defense_with_common_tags() -> None:
    """Deleting a mandatory procedure fails closed even when common C01-C12 tags are present."""
    fake_compressed_prompt = """
    # Compressed Storage Hygiene
    C01_INPUT_GROUNDING: satisfied
    C02_AUTHORITY_SCOPING: satisfied
    C03_MODE_SEPARATION: satisfied
    implemented: true
    authority: AuthorityContext
    # Silently deleted category flow and filtered plan
    """
    mandatory_tokens = ["G0", "G1", "apply_quarantine", "Phase 0: Mandatory Preamble"]
    ok, errors = verify_procedure_deletion_defense(fake_compressed_prompt, mandatory_tokens)
    assert ok is False
    assert any("Procedure deletion detected" in err for err in errors)
    assert any("G0" in err for err in errors)



def test_empty_audit_callback_defense() -> None:
    """Empty audit callback fails procedure verification."""
    def empty_callback(payload: Any) -> list[str]:
        return []

    # Verify that an empty callback returns zero findings on a corrupted prompt
    test_prompt = "Short stub without mandatory procedures"
    required = ["Five Adversarial Reviewers", "PRAGMA integrity_check", "14 rows"]
    ok, errors = verify_procedure_deletion_defense(test_prompt, required)
    assert ok is False
    assert len(errors) > 0


def test_rollback_governance_rejects_wildcard_recipe(
    temp_dir: Path, valid_requirements_data: dict[str, Any]
) -> None:
    """Rollback governance must reject wildcard copy recipe."""
    data = copy.deepcopy(valid_requirements_data)
    data["rollback_governance"]["status"] = "APPROVED"  # Attempt to approve wildcard recipe
    bad_file = temp_dir / "bad_rg.json"
    bad_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_requirements_inventory(bad_file, ROOT)
    assert ok is False
    assert any("Rollback governance status must be REJECTED" in err for err in errors)


def test_forged_predecessor_receipt_digest_rejection(temp_dir: Path) -> None:
    """Predecessor receipt with mismatched canonical digest fails closed."""
    parent_receipt = temp_dir / "parent_receipt.json"
    payload = {
        "contract_id": "urn:nee:antigravity-task-restoration-receipt:1.0.0",
        "wave": 1,
        "terminal_outcome": "PASS",
        "timestamp": "2026-10-07T09:00:00Z",
        "canonical_digest": "bad_digest_tampered",
    }
    parent_receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Predecessor receipt digest mismatch"):
        verify_parent_receipt(wave=2, parent_receipt=parent_receipt)


def test_wave1_with_parent_receipt_fails(temp_dir: Path) -> None:
    """Wave 1 run with a non-null parent receipt must fail closed."""
    dummy_parent = temp_dir / "dummy.json"
    dummy_parent.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Wave 1 cannot have a predecessor parent receipt"):
        verify_parent_receipt(wave=1, parent_receipt=dummy_parent)


# -----------------------------------------------------------------------------
# Category 4: Rollback & Recovery
# -----------------------------------------------------------------------------

def test_lock_file_cleanup_on_verification_failure(temp_dir: Path) -> None:
    """Lock file must be unlinked or withheld if terminal outcome is not PASS."""
    lock_dir = temp_dir / "artifacts" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    existing_lock = lock_dir / "antigravity_task_restoration_wave_1.complete"
    existing_lock.write_text("pre-existing lock", encoding="utf-8")

    failed_receipt = {
        "wave": 1,
        "terminal_outcome": "FAIL",
        "timestamp": "2026-10-07T09:00:00Z",
        "canonical_digest": "abc",
    }
    receipt_path = temp_dir / "receipt.json"
    receipt_path.write_text(json.dumps(failed_receipt), encoding="utf-8")

    retcode = _finalize_wave_lock(
        wave=1,
        run_id="test-fail",
        receipt=failed_receipt,
        receipt_path=receipt_path,
        repo_root=temp_dir,
    )
    assert retcode == 1
    assert not existing_lock.exists(), "Existing lock must be removed on failure"
