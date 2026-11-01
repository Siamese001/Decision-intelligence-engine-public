"""tests.test_scheduled_ingestion_recovery

Wave 4 Track 4B test suite verifying staging and package recovery validation,
digest mismatch detection, supersession spoofing prevention, approval scope binding,
and storage pinning.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.staging_recovery import (
    ApprovalScopeMismatchError,
    DigestMismatchError,
    IngestionRecoveryPackage,
    MissingPackageError,
    SupersessionSpoofingError,
    assert_no_unauthorized_supersession,
    is_staging_package_pinned,
    pin_staging_package,
    unpin_staging_package,
    validate_staging_package,
)


def test_missing_package_blocks_with_actionable_recovery_package(tmp_path: Path) -> None:
    """Acceptance criterion: Missing package blocks before execution with an actionable recovery package."""
    missing_path = tmp_path / "non_existent_package.json"
    valid, recovery = validate_staging_package(
        staging_path=missing_path,
        expected_digest="abcd1234ef",
        operation_id="op_missing_01",
        directive_id="dir_missing_01",
    )
    assert valid is False
    assert recovery is not None
    assert recovery.intended_resume_stage == "U0_STAGING"
    assert any("missing" in p.lower() for p in recovery.missing_prerequisites)

    # Validate against schema
    recovery_file = tmp_path / "recovery_pkg.json"
    recovery.save(recovery_file)
    assert recovery_file.is_file()


def test_digest_mismatch_blocks_with_recovery_package(tmp_path: Path) -> None:
    """Acceptance criterion: Digest-mismatched package blocks before execution with recovery package."""
    pkg_file = tmp_path / "corrupted_package.json"
    pkg_file.write_text('{"content": "tampered"}', encoding="utf-8")
    actual_hash = hashlib.sha256(pkg_file.read_bytes()).hexdigest()
    expected_hash = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"

    valid, recovery = validate_staging_package(
        staging_path=pkg_file,
        expected_digest=expected_hash,
        operation_id="op_digest_01",
        directive_id="dir_digest_01",
    )
    assert valid is False
    assert recovery is not None
    assert any("digest mismatch" in p.lower() for p in recovery.missing_prerequisites)
    assert recovery.package_digest == actual_hash


def test_supersession_spoofing_rejected() -> None:
    """Acceptance criterion: A similar filename, suffix, or timestamp never establishes supersession authority."""
    orig = Path("/staging/project_review_final.json")
    cand_suffix = Path("/staging/project_review_final_v2.json")
    cand_timestamp = Path("/staging/project_review_final_20261006.json")

    with pytest.raises(SupersessionSpoofingError, match="Unauthorized supersession attempt"):
        assert_no_unauthorized_supersession(orig, cand_suffix)

    with pytest.raises(SupersessionSpoofingError, match="Unauthorized supersession attempt"):
        assert_no_unauthorized_supersession(orig, cand_timestamp)

    # Identical path is allowed
    assert_no_unauthorized_supersession(orig, orig)


def test_approval_scope_mismatch_requires_renewal(tmp_path: Path) -> None:
    """Acceptance criterion: Changed scope requires renewed authority."""
    pkg_file = tmp_path / "valid_pkg.json"
    pkg_file.write_text('{"items": ["a", "b", "c"]}', encoding="utf-8")
    digest = hashlib.sha256(pkg_file.read_bytes()).hexdigest()

    # Scope was approved for only {"item_a", "item_b"}, but current is {"item_a", "item_b", "item_c"}
    valid, recovery = validate_staging_package(
        staging_path=pkg_file,
        expected_digest=digest,
        operation_id="op_scope_01",
        directive_id="dir_scope_01",
        approval_token="appr_tok_123",
        approved_digest=digest,
        approved_scope={"item_a", "item_b"},
        current_scope={"item_a", "item_b", "item_c"},
    )
    assert valid is False
    assert recovery is not None
    assert recovery.current_approval_applicability.scope_match is False
    assert recovery.current_approval_applicability.renewal_required is True


def test_storage_pinning_prevents_stranding(tmp_path: Path) -> None:
    """Acceptance criterion: Storage pins exact packages needed by open operations so cleanup cannot strand work."""
    pin_registry = tmp_path / "pins" / "package_pins.json"
    package_a = tmp_path / "staging" / "meeting_a.json"
    package_a.parent.mkdir(parents=True)
    package_a.write_text("{}", encoding="utf-8")

    assert is_staging_package_pinned(package_a, pin_registry) is False

    # Pin for operation 1
    pin_staging_package(package_a, "op_01", pin_registry)
    assert is_staging_package_pinned(package_a, pin_registry) is True

    # Pin for operation 2
    pin_staging_package(package_a, "op_02", pin_registry)
    assert is_staging_package_pinned(package_a, pin_registry) is True

    # Unpin operation 1, still pinned by op 2
    unpin_staging_package(package_a, "op_01", pin_registry)
    assert is_staging_package_pinned(package_a, pin_registry) is True

    # Unpin operation 2, now unpinned
    unpin_staging_package(package_a, "op_02", pin_registry)
    assert is_staging_package_pinned(package_a, pin_registry) is False


def test_valid_staging_package_passes(tmp_path: Path) -> None:
    """Verifies that uncorrupted package with matching approval and scope passes cleanly."""
    pkg_file = tmp_path / "clean_pkg.json"
    pkg_file.write_text('{"status": "ready"}', encoding="utf-8")
    digest = hashlib.sha256(pkg_file.read_bytes()).hexdigest()

    valid, recovery = validate_staging_package(
        staging_path=pkg_file,
        expected_digest=digest,
        operation_id="op_clean_01",
        directive_id="dir_clean_01",
        approval_token="appr_valid",
        approved_digest=digest,
        approved_scope={"ready"},
        current_scope={"ready"},
    )
    assert valid is True
    assert recovery is None
