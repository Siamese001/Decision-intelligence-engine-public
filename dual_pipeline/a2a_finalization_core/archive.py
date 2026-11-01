"""Filesystem archive and staging operations for A2A finalization."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from dual_pipeline.core import assert_safe_mutable_path, sha256_bytes, sha256_file

from .constants import (
    _RUN_ID_PATTERN,
    _WINDOWS_RESERVED_PATH_SEGMENT,
    ARCHIVED_SOURCE_BUNDLE_FILENAME,
)
from .models import A2AFinalizationError, verified_finalization_binding


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise A2AFinalizationError(f"{label} is not valid JSON") from exc
    return _read_object_bytes(payload, label=label)


def _read_object_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A2AFinalizationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise A2AFinalizationError(f"{label} must be a JSON object")
    return value


def _relative_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise A2AFinalizationError(f"{label} must be a bounded nonempty relative path")
    path = Path(value)
    if (
        path.is_absolute()
        or path.drive
        or re.match(r"^[A-Za-z]:", value)
        or any(
            part in {"", ".", ".."}
            or ":" in part
            or part.rstrip(" .") != part
            or _WINDOWS_RESERVED_PATH_SEGMENT.fullmatch(part) is not None
            for part in path.parts
        )
    ):
        raise A2AFinalizationError(f"{label} must be a safe relative path")
    return path


def _archive_path_key(path: Path) -> str:
    """Return the portable physical-name key for a controlled archive path."""
    return path.as_posix().casefold()


def _safe_archive_root_id(value: object) -> str:
    """Validate one portable, non-aliasing source-capture root component."""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
    ):
        raise A2AFinalizationError("A2A archive root name is not a valid root ID")
    try:
        path = _relative_path(value, label="A2A archive root name")
    except A2AFinalizationError as exc:
        raise A2AFinalizationError("A2A archive root name is not a valid root ID") from exc
    if len(path.parts) != 1 or path.name != value:
        raise A2AFinalizationError("A2A archive root name is not a valid root ID")
    return value


def _safe_archive_file(archive_root: Path, relative_path: object, *, label: str) -> Path:
    relative = _relative_path(relative_path, label=label)
    target = assert_safe_mutable_path(archive_root, archive_root / relative, label=label)
    if not target.is_file():
        raise A2AFinalizationError(f"{label} is missing")
    return target


def _copy_archive_file(
    source: Path,
    archive_root: Path,
    relative_path: str,
    *,
    role: str,
) -> dict[str, str]:
    source = source.resolve()
    if not source.is_file():
        raise A2AFinalizationError(f"A2A {role} source file is missing")
    target = assert_safe_mutable_path(
        archive_root,
        archive_root / _relative_path(relative_path, label=f"A2A {role} path"),
        label=f"A2A {role} archive target",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_digest = sha256_file(source)
    if target.exists():
        if not target.is_file() or sha256_file(target) != expected_digest:
            raise A2AFinalizationError(
                f"A2A {role} archive target already exists with different bytes"
            )
    else:
        shutil.copyfile(source, target)
    if sha256_file(target) != expected_digest:
        raise A2AFinalizationError(f"A2A {role} archive copy hash mismatch")
    return {"role": role, "relative_path": relative_path, "sha256": expected_digest}


def stage_finalization_archive(
    *,
    runtime_root: Path,
    archive_root_id: str,
    bundle_path: Path,
    capture_manifest_path: Path,
    span_ledger_path: Path,
    selection_path: Path,
) -> dict[str, Any]:
    """Store exact finalizer inputs beneath one controlled capture-archive root."""
    raw_runtime_root = Path(runtime_root).expanduser().absolute()
    archive_root_id = _safe_archive_root_id(archive_root_id)
    raw_runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_root = assert_safe_mutable_path(
        raw_runtime_root, raw_runtime_root, label="A2A finalization runtime root"
    )
    source_captures = assert_safe_mutable_path(
        runtime_root,
        runtime_root / "source_captures",
        label="A2A finalization source captures root",
    )
    source_captures.mkdir(parents=True, exist_ok=True)
    for existing in source_captures.iterdir():
        if existing.name.casefold() == archive_root_id.casefold() and existing.name != archive_root_id:
            raise A2AFinalizationError(
                "A2A archive root name case-collides with an existing capture archive"
            )
    archive_root = assert_safe_mutable_path(
        runtime_root,
        source_captures / archive_root_id,
        label="A2A finalization archive root",
    )
    archive_root.mkdir(parents=True, exist_ok=True)
    capture_manifest_path = Path(capture_manifest_path).resolve()
    capture_manifest = _read_object(capture_manifest_path, label="A2A capture manifest")
    captures = capture_manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        raise A2AFinalizationError("A2A capture manifest has no captures")

    rows = [
        _copy_archive_file(
            Path(bundle_path),
            archive_root,
            ARCHIVED_SOURCE_BUNDLE_FILENAME,
            role="source_bundle",
        ),
        _copy_archive_file(
            capture_manifest_path,
            archive_root,
            "capture_manifest.json",
            role="capture_manifest",
        ),
        _copy_archive_file(
            Path(span_ledger_path),
            archive_root,
            "source_span_ledger.json",
            role="span_ledger",
        ),
        _copy_archive_file(
            Path(selection_path),
            archive_root,
            "source_span_selection.json",
            role="selection_ledger",
        ),
    ]
    manifest_root = capture_manifest_path.parent.resolve()
    for capture in sorted(captures, key=lambda item: str(item.get("source_id", ""))):
        if not isinstance(capture, dict):
            raise A2AFinalizationError("A2A capture manifest has an invalid capture row")
        raw_relative = _relative_path(capture.get("raw_path"), label="A2A raw capture path")
        source_raw = (manifest_root / raw_relative).resolve()
        try:
            source_raw.relative_to(manifest_root)
        except ValueError as exc:
            raise A2AFinalizationError("A2A raw capture escapes its source manifest") from exc
        expected_raw_digest = capture.get("raw_sha256")
        if (
            not isinstance(expected_raw_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_raw_digest)
            or not source_raw.is_file()
            or sha256_file(source_raw) != expected_raw_digest
        ):
            raise A2AFinalizationError("A2A raw capture does not match its capture manifest")
        rows.append(
            _copy_archive_file(
                source_raw,
                archive_root,
                raw_relative.as_posix(),
                role="raw_capture",
            )
        )
    if len({_archive_path_key(Path(row["relative_path"])) for row in rows}) != len(rows):
        raise A2AFinalizationError("A2A archive contains colliding relative paths")
    return {
        "root_id": archive_root_id,
        "files": sorted(rows, key=lambda row: row["relative_path"]),
    }


def _stage_exact_bytes(destination: Path, payload: bytes, *, label: str) -> None:
    """Create one immutable byte snapshot, retaining only an exact replay."""
    expected = sha256_bytes(payload)
    if destination.exists():
        if not destination.is_file() or sha256_file(destination) != expected:
            raise A2AFinalizationError(f"{label} already exists with different bytes")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
            stream.flush()
    except FileExistsError:
        if not destination.is_file() or sha256_file(destination) != expected:
            raise A2AFinalizationError(f"{label} already exists with different bytes") from None
    if not destination.is_file() or sha256_file(destination) != expected:
        raise A2AFinalizationError(f"{label} byte snapshot verification failed")


def stage_ingress_finalization_evidence(
    *,
    runtime_root: Path,
    run_id: str,
    package_bytes: bytes,
    receipt_bytes: bytes,
    verification: object,
) -> dict[str, str]:
    """Persist exact verifier inputs under the intake run for later revalidation."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise A2AFinalizationError("A2A finalization evidence has an unsafe intake run ID")
    package = _read_object_bytes(package_bytes, label="A2A finalized package")
    binding = verified_finalization_binding(verification, package=package)
    if binding["package_file_sha256"] != sha256_bytes(package_bytes):
        raise A2AFinalizationError("A2A verifier capability does not match packet bytes")
    if binding["receipt_file_sha256"] != sha256_bytes(receipt_bytes):
        raise A2AFinalizationError("A2A verifier capability does not match receipt bytes")

    raw_runtime_root = Path(runtime_root).expanduser().absolute()
    raw_runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_root = assert_safe_mutable_path(
        raw_runtime_root, raw_runtime_root, label="A2A finalization runtime root"
    )
    evidence_root = assert_safe_mutable_path(
        runtime_root,
        runtime_root / "runs" / run_id / "a2a_finalization",
        label="A2A finalization intake evidence directory",
    )
    package_relative_path = f"runs/{run_id}/a2a_finalization/finalized_packet.json"
    receipt_relative_path = f"runs/{run_id}/a2a_finalization/finalization_receipt.json"
    package_target = assert_safe_mutable_path(
        runtime_root,
        runtime_root / _relative_path(package_relative_path, label="A2A packet snapshot path"),
        label="A2A packet snapshot",
    )
    receipt_target = assert_safe_mutable_path(
        runtime_root,
        runtime_root / _relative_path(receipt_relative_path, label="A2A receipt snapshot path"),
        label="A2A receipt snapshot",
    )
    if package_target.parent != evidence_root or receipt_target.parent != evidence_root:
        raise A2AFinalizationError("A2A finalization evidence path is not canonical")
    _stage_exact_bytes(package_target, package_bytes, label="A2A finalized packet snapshot")
    _stage_exact_bytes(receipt_target, receipt_bytes, label="A2A finalization receipt snapshot")
    return {
        "run_id": run_id,
        "package_relative_path": package_relative_path,
        "receipt_relative_path": receipt_relative_path,
        "package_file_sha256": binding["package_file_sha256"],
        "receipt_file_sha256": binding["receipt_file_sha256"],
        "archive_root_id": binding["archive_root_id"],
    }
