"""tests.test_scheduled_dlq_lifecycle

Wave 4 Track 4A test suite verifying DLQ failure classification,
incident fingerprints, occurrence history, retry limits, corrupt record exposure,
runtime root isolation, and status transition validation.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.dlq.lifecycle import (
    DLQStatus,
    FailureCategory,
    assess_dlq_migration,
    classify_failure,
    compute_incident_fingerprint,
    validate_status_transition,
)
from dual_pipeline.dlq.manager import DeadLetterQueueManager


def test_failure_classification() -> None:
    """Verifies classification into HUMAN_WAIT, TRANSIENT, PERMANENT, and UNKNOWN."""
    class QuotaError(Exception):
        pass

    assert classify_failure(QuotaError("credit_balance_exhausted")) == FailureCategory.HUMAN_WAIT
    assert classify_failure(Exception("429 Too Many Requests")) == FailureCategory.TRANSIENT
    assert classify_failure(Exception("Connection reset by peer")) == FailureCategory.TRANSIENT
    assert classify_failure(ValueError("Invalid schema field")) == FailureCategory.PERMANENT
    assert classify_failure(FileNotFoundError("Missing file")) == FailureCategory.PERMANENT
    assert classify_failure(RuntimeError("Unexpected arithmetic error")) == FailureCategory.UNKNOWN


def test_repeated_approval_waits_do_not_produce_independent_new_incidents(tmp_path: Path) -> None:
    """Acceptance criterion: Repeated approval waits do not produce independent NEW incidents."""
    runtime_root = tmp_path / "runtime_01"
    dlq = DeadLetterQueueManager(runtime_root=runtime_root)

    metadata = {
        "directive_id": "dir-20261006-01",
        "project": "project_alpha",
        "approval_required": True,
    }
    quota_exc = Exception("credit_balance_exhausted: operator top-up needed")

    # First wait
    dlq_id_1 = dlq.enqueue(
        input_path="/path/to/meeting.json",
        metadata=metadata,
        error=quota_exc,
        stage="U0_STAGING",
    )
    item_1 = dlq.get_item(dlq_id_1)
    assert item_1 is not None
    assert item_1["status"] == DLQStatus.WAITING_APPROVAL.value
    assert item_1["occurrence_count"] == 1
    assert len(item_1["occurrences"]) == 1

    # Repeated wait on subsequent scheduled cycle
    dlq_id_2 = dlq.enqueue(
        input_path="/path/to/meeting.json",
        metadata=metadata,
        error=quota_exc,
        stage="U0_STAGING",
    )
    assert dlq_id_2 == dlq_id_1  # Reuses same incident!

    item_2 = dlq.get_item(dlq_id_1)
    assert item_2["status"] == DLQStatus.WAITING_APPROVAL.value
    assert item_2["occurrence_count"] == 2
    assert len(item_2["occurrences"]) == 2

    # Verify directory only has 1 record
    all_items = dlq.list_items()
    assert len(all_items) == 1


def test_transient_failure_retry_limit_and_exhaustion(tmp_path: Path) -> None:
    """Acceptance criterion: Transient retry limits are explicit; does not blindly replay."""
    runtime_root = tmp_path / "runtime_transient"
    dlq = DeadLetterQueueManager(runtime_root=runtime_root)

    metadata = {
        "directive_id": "dir-transient-01",
        "max_retries": 3,
    }
    transient_exc = Exception("503 Service Unavailable: upstream timeout")

    # Initial transient failure
    dlq_id = dlq.enqueue(input_path="/path/test.json", metadata=metadata, error=transient_exc, stage="L1_TRANSCRIBE")
    assert dlq.get_item(dlq_id)["status"] == DLQStatus.TRANSIENT_RETRY.value
    assert dlq.get_item(dlq_id)["retry_count"] == 0

    # Retry 1
    dlq.enqueue(input_path="/path/test.json", metadata=metadata, error=transient_exc, stage="L1_TRANSCRIBE")
    assert dlq.get_item(dlq_id)["status"] == DLQStatus.TRANSIENT_RETRY.value
    assert dlq.get_item(dlq_id)["retry_count"] == 1

    # Retry 2
    dlq.enqueue(input_path="/path/test.json", metadata=metadata, error=transient_exc, stage="L1_TRANSCRIBE")
    assert dlq.get_item(dlq_id)["status"] == DLQStatus.TRANSIENT_RETRY.value
    assert dlq.get_item(dlq_id)["retry_count"] == 2

    # Retry 3 -> hits limit!
    dlq.enqueue(input_path="/path/test.json", metadata=metadata, error=transient_exc, stage="L1_TRANSCRIBE")
    item = dlq.get_item(dlq_id)
    assert item["status"] == DLQStatus.RETRY_EXHAUSTED.value
    assert item["retry_count"] == 3


def test_permanent_failure_remains_visible(tmp_path: Path) -> None:
    """Acceptance criterion: Permanent/unknown failures are visible and not blindly replayed."""
    runtime_root = tmp_path / "runtime_perm"
    dlq = DeadLetterQueueManager(runtime_root=runtime_root)

    metadata = {"directive_id": "dir-perm-01"}
    perm_exc = ValueError("Malformed document payload: missing required field 'title'")

    dlq_id = dlq.enqueue(input_path="/path/doc.json", metadata=metadata, error=perm_exc, stage="C0_INGRESS")
    item = dlq.get_item(dlq_id)
    assert item["status"] == DLQStatus.NEW.value
    assert item["failure_category"] == FailureCategory.PERMANENT.value
    assert item["retry_count"] == 0


def test_fingerprints_keep_distinct_operations_separate(tmp_path: Path) -> None:
    """Acceptance criterion: Different operations stay distinct."""
    runtime_root = tmp_path / "runtime_fp"
    dlq = DeadLetterQueueManager(runtime_root=runtime_root)

    err = Exception("Connection reset")
    id1 = dlq.enqueue("/path/op1.json", {"directive_id": "op1"}, err, "U0")
    id2 = dlq.enqueue("/path/op2.json", {"directive_id": "op2"}, err, "U0")

    assert id1 != id2
    item1 = dlq.get_item(id1)
    item2 = dlq.get_item(id2)
    assert item1["fingerprint"] != item2["fingerprint"]
    assert len(dlq.list_items()) == 2


def test_runtime_root_isolation_prevents_cross_root_writes(tmp_path: Path) -> None:
    """Acceptance criterion: Assert no cross-root writes."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"

    dlq_a = DeadLetterQueueManager(runtime_root=root_a)
    dlq_b = DeadLetterQueueManager(runtime_root=root_b)

    id_a = dlq_a.enqueue("/path/a.json", {"directive_id": "op_a"}, Exception("Error A"), "L0")
    assert (root_a / "dlq" / f"{id_a}.json").exists()
    assert not (root_b / "dlq").exists()

    id_b = dlq_b.enqueue("/path/b.json", {"directive_id": "op_b"}, Exception("Error B"), "L0")
    assert (root_b / "dlq" / f"{id_b}.json").exists()

    assert len(dlq_a.list_items()) == 1
    assert len(dlq_b.list_items()) == 1


def test_corrupt_records_exposed_not_swallowed(tmp_path: Path) -> None:
    """Acceptance criterion: Expose corrupt records."""
    runtime_root = tmp_path / "runtime_corrupt"
    dlq = DeadLetterQueueManager(runtime_root=runtime_root)

    # Write a valid record
    dlq.enqueue("/path/valid.json", {"directive_id": "valid_01"}, Exception("Valid error"), "U0")

    # Inject a corrupt record (not valid JSON)
    corrupt_file = dlq.dlq_dir / "dlq_20261006_corrupt.json"
    corrupt_file.write_text("{this is not valid json...", encoding="utf-8")

    items = dlq.list_items()
    assert len(items) == 2
    corrupt_items = [it for it in items if it.get("status") == DLQStatus.CORRUPT.value]
    assert len(corrupt_items) == 1
    assert corrupt_items[0]["dlq_id"] == "dlq_20261006_corrupt"


def test_status_transition_rules_and_resolution_proof(tmp_path: Path) -> None:
    """Acceptance criterion: Require verified recovery evidence for resolution."""
    runtime_root = tmp_path / "runtime_status"
    dlq = DeadLetterQueueManager(runtime_root=runtime_root)

    dlq_id = dlq.enqueue("/path/res.json", {"directive_id": "res_01"}, Exception("Err"), "L1")

    # Resolving without evidence fails closed
    with pytest.raises(ValueError, match="requires verified resolution notes"):
        dlq.mark_status(dlq_id, DLQStatus.RESOLVED.value, notes=None)

    with pytest.raises(ValueError, match="requires verified resolution notes"):
        dlq.mark_status(dlq_id, DLQStatus.RESOLVED.value, notes="   ")

    # Resolving with proof succeeds
    assert dlq.mark_status(dlq_id, DLQStatus.RESOLVED.value, notes="Directive re-staged and validated")
    resolved_item = dlq.get_item(dlq_id)
    assert resolved_item["status"] == DLQStatus.RESOLVED.value
    assert resolved_item["resolved_at"] is not None

    # Cannot transition out of terminal state
    with pytest.raises(ValueError, match="Illegal DLQ status transition"):
        dlq.mark_status(dlq_id, DLQStatus.NEW.value)


def test_read_only_dlq_manager_does_not_mutate(tmp_path: Path) -> None:
    """Acceptance criterion: Read-only tasks never instantiate write-producing queue paths merely to inspect them."""
    non_existent_root = tmp_path / "non_existent_runtime"
    dlq_ro = DeadLetterQueueManager(runtime_root=non_existent_root, read_only=True)

    # Directory was NOT created
    assert not (non_existent_root / "dlq").exists()
    assert dlq_ro.list_items() == []

    with pytest.raises(PermissionError, match="Cannot enqueue to DLQ in read-only mode"):
        dlq_ro.enqueue("/path/fail.json", {}, Exception("Blocked"), "U0")


def test_migration_assessment(tmp_path: Path) -> None:
    """Acceptance criterion: Migration assessment reports counts and ambiguous mappings."""
    dlq_dir = tmp_path / "legacy_dlq"
    dlq_dir.mkdir(parents=True)

    # Legacy record without fingerprint or failure_category
    legacy = {
        "dlq_id": "dlq_legacy_01",
        "status": "NEW",
        "error_message": "Legacy failure",
    }
    (dlq_dir / "dlq_legacy_01.json").write_text(json.dumps(legacy), encoding="utf-8")

    # Corrupt record
    (dlq_dir / "dlq_legacy_02.json").write_text("corrupt content", encoding="utf-8")

    report = assess_dlq_migration(dlq_dir)
    assert report["total_records"] == 2
    assert report["valid_records"] == 1
    assert report["corrupt_records"] == 1
    assert report["unmigrated_records"] == 1
    assert report["status_counts"]["NEW"] == 1
