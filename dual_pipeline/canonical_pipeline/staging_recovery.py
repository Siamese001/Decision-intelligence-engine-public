"""dual_pipeline.canonical_pipeline.staging_recovery

Implements staging and package recovery validation, ensuring that missing or
digest-mismatched ingestion packages halt before execution with an actionable
recovery package, preventing supersession spoofing and storage cleanup stranding.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

CONTRACT_ID = "urn:nee:ingestion-recovery-package:1.0.0"
SCHEMA_VERSION = "1.0.0"


class StagingRecoveryError(Exception):
    """Base error for staging and recovery validation."""


class MissingPackageError(StagingRecoveryError):
    """Raised when an ingestion package is missing from staging."""


class DigestMismatchError(StagingRecoveryError):
    """Raised when a staging package SHA-256 does not match the directive binding."""


class SupersessionSpoofingError(StagingRecoveryError):
    """Raised when a similar filename, suffix, or timestamp is falsely claimed as supersession authority."""


class ApprovalScopeMismatchError(StagingRecoveryError):
    """Raised when an approval token does not bind the recovered package scope."""


@dataclass
class ApprovalApplicability:
    approval_valid: bool
    scope_match: bool
    renewal_required: bool
    approval_token: str | None = None


@dataclass
class IngestionRecoveryPackage:
    package_id: str
    operation_id: str
    directive_id: str
    package_digest: str
    missing_prerequisites: list[str]
    current_approval_applicability: ApprovalApplicability
    intended_resume_stage: str
    predicted_effects: list[str]
    rollback_conditions: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": CONTRACT_ID,
            "schema_version": SCHEMA_VERSION,
            "package_id": self.package_id,
            "operation_id": self.operation_id,
            "directive_id": self.directive_id,
            "package_digest": self.package_digest,
            "missing_prerequisites": list(self.missing_prerequisites),
            "current_approval_applicability": asdict(self.current_approval_applicability),
            "intended_resume_stage": self.intended_resume_stage,
            "predicted_effects": list(self.predicted_effects),
            "rollback_conditions": list(self.rollback_conditions),
            "created_at": self.created_at,
        }

    def validate_schema(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema_path = root / "contracts" / "ingestion-recovery-package.schema.json"
        if not schema_path.is_file():
            raise FileNotFoundError(f"Schema not found: {schema_path}")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=self.to_dict(), schema=schema)

    def save(self, path: Path) -> None:
        self.validate_schema()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    if not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def assert_no_unauthorized_supersession(current_package_path: Path, proposed_replacement_path: Path) -> None:
    """Enforces that a similar filename, suffix, or timestamp never establishes supersession or cancellation authority."""
    if current_package_path.name == proposed_replacement_path.name:
        return

    # Check for naive suffix/timestamp alterations (e.g. foo_v2.json, foo_20261006.json)
    curr_stem = current_package_path.stem
    prop_stem = proposed_replacement_path.stem

    if prop_stem.startswith(curr_stem) or curr_stem.startswith(prop_stem):
        raise SupersessionSpoofingError(
            f"Unauthorized supersession attempt: similar filename '{proposed_replacement_path.name}' "
            f"cannot supersede '{current_package_path.name}' without explicit signed operator supersession authority."
        )


def validate_staging_package(
    staging_path: Path,
    expected_digest: str | None = None,
    operation_id: str = "op_default",
    directive_id: str = "dir_default",
    approval_token: str | None = None,
    approved_digest: str | None = None,
    approved_scope: set[str] | None = None,
    current_scope: set[str] | None = None,
) -> tuple[bool, IngestionRecoveryPackage | None]:
    """Validates that a staging package is present and uncorrupted before execution begins."""
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    missing_prereqs: list[str] = []
    actual_digest = ""

    if not staging_path.is_file():
        missing_prereqs.append(f"Staging package file missing: {staging_path}")
    else:
        actual_digest = compute_file_sha256(staging_path)
        if expected_digest and actual_digest != expected_digest:
            missing_prereqs.append(
                f"Package digest mismatch: expected '{expected_digest}', observed '{actual_digest}'"
            )

    # Check approval applicability
    scope_match = True
    renewal_required = False
    approval_valid = False

    if approval_token:
        approval_valid = True
        if approved_digest and expected_digest and approved_digest != expected_digest:
            approval_valid = False
            renewal_required = True
        if approved_scope is not None and current_scope is not None:
            if approved_scope != current_scope:
                scope_match = False
                renewal_required = True
                approval_valid = False

    if missing_prereqs or renewal_required:
        recovery = IngestionRecoveryPackage(
            package_id=f"pkg-rec-{directive_id}-{operation_id}",
            operation_id=operation_id,
            directive_id=directive_id,
            package_digest=actual_digest or (expected_digest or "UNKNOWN"),
            missing_prerequisites=missing_prereqs,
            current_approval_applicability=ApprovalApplicability(
                approval_valid=approval_valid,
                approval_token=approval_token,
                scope_match=scope_match,
                renewal_required=renewal_required,
            ),
            intended_resume_stage="U0_STAGING",
            predicted_effects=[
                "Halt pipeline before destructive graph or release mutation",
                "Emit recovery package to operator review ledger",
                "Pin existing artifacts against cleanup pruner",
            ],
            rollback_conditions=[
                "Restore original staging package if present in shadow cache",
                "Re-request authenticated operator approval if scope altered",
            ],
            created_at=now_iso,
        )
        return False, recovery

    return True, None


def pin_staging_package(package_path: Path, operation_id: str, pin_registry_path: Path) -> None:
    """Pins a staging package so that scheduled storage cleanup tasks cannot prune or strand it."""
    pin_registry_path.parent.mkdir(parents=True, exist_ok=True)
    pins: dict[str, list[str]] = {}
    if pin_registry_path.is_file():
        try:
            pins = json.loads(pin_registry_path.read_text(encoding="utf-8"))
        except Exception:
            pins = {}

    key = str(package_path.resolve())
    op_list = pins.get(key, [])
    if operation_id not in op_list:
        op_list.append(operation_id)
    pins[key] = op_list

    pin_registry_path.write_text(json.dumps(pins, indent=2), encoding="utf-8")


def is_staging_package_pinned(package_path: Path, pin_registry_path: Path) -> bool:
    """Checks whether a staging package is currently pinned by an open operation."""
    if not pin_registry_path.is_file():
        return False
    try:
        pins = json.loads(pin_registry_path.read_text(encoding="utf-8"))
        key = str(package_path.resolve())
        return len(pins.get(key, [])) > 0
    except Exception:
        return False


def unpin_staging_package(package_path: Path, operation_id: str, pin_registry_path: Path) -> None:
    """Unpins a staging package when the operation completes or resolves."""
    if not pin_registry_path.is_file():
        return
    try:
        pins = json.loads(pin_registry_path.read_text(encoding="utf-8"))
        key = str(package_path.resolve())
        if key in pins:
            pins[key] = [op for op in pins[key] if op != operation_id]
            if not pins[key]:
                del pins[key]
            pin_registry_path.write_text(json.dumps(pins, indent=2), encoding="utf-8")
    except Exception:
        pass
