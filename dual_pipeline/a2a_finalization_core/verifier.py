"""Verification, receipt creation, and replay for finalized A2A packages."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    digest,
    sha256_bytes,
    sha256_file,
    stable_id,
)

from .archive import (
    _archive_path_key,
    _read_object,
    _read_object_bytes,
    _relative_path,
    _safe_archive_file,
    _safe_archive_root_id,
)
from .constants import (
    _ARCHIVE_ROLES,
    _FINALIZATION_BINDING_FIELDS,
    _FINALIZATION_REFERENCE_FIELDS,
    _REQUIRED_ARCHIVE_ROLES,
    _RUN_ID_PATTERN,
    _VERIFIER_CAPABILITY,
    FINALIZATION_RECEIPT_CONTRACT,
    FINALIZER_NAME,
    FINALIZER_VERSION,
)
from .models import (
    A2AFinalizationError,
    VerifiedA2AFinalization,
    verified_finalization_binding,
)


def create_finalization_receipt(
    *,
    source_bundle: dict[str, str],
    capture: dict[str, str],
    archive: dict[str, Any],
    packages: list[dict[str, str]],
    audiences: list[str],
) -> dict[str, Any]:
    """Create a sealed receipt binding finalizer output to its archived inputs."""
    receipt_id = stable_id(
        "a2a-finalization-receipt",
        source_bundle["sha256"],
        capture["capture_manifest_sha256"],
        capture["span_ledger_sha256"],
        capture["selection_ledger_sha256"],
        digest(archive),
        *(row["file_sha256"] for row in sorted(packages, key=lambda value: value["relative_path"])),
        length=30,
    )
    return create_artifact(
        "a2a-finalization-receipt",
        receipt_id,
        receipt_id=receipt_id,
        status="PASS",
        source_bundle=source_bundle,
        capture=capture,
        archive=archive,
        packages=sorted(packages, key=lambda row: row["relative_path"]),
        audiences=list(audiences),
        finalizer={"name": FINALIZER_NAME, "version": FINALIZER_VERSION},
    )


def _verify_receipt_archive(
    receipt: dict[str, Any], *, runtime_root: Path
) -> tuple[Path, dict[str, Path], dict[str, str]]:
    archive = receipt["archive"]
    root_id = _safe_archive_root_id(archive["root_id"])
    runtime_root = Path(runtime_root).expanduser().absolute()
    archive_root = assert_safe_mutable_path(
        runtime_root,
        runtime_root / "source_captures" / root_id,
        label="A2A receipt archive root",
    )
    if not archive_root.is_dir():
        raise A2AFinalizationError("A2A receipt archive root is unavailable")
    files = archive["files"]
    if not isinstance(files, list):
        raise A2AFinalizationError("A2A receipt archive file inventory is invalid")
    paths_by_role: dict[str, Path] = {}
    seen_paths: set[str] = set()
    raw_capture_digests: dict[str, str] = {}
    for row in files:
        if not isinstance(row, dict) or set(row) != {"role", "relative_path", "sha256"}:
            raise A2AFinalizationError("A2A receipt archive row has an invalid shape")
        role = row["role"]
        if role not in _ARCHIVE_ROLES or not isinstance(row["sha256"], str):
            raise A2AFinalizationError("A2A receipt archive row has an invalid role or digest")
        relative = _relative_path(row["relative_path"], label="A2A receipt archive path")
        rendered = relative.as_posix()
        path_key = _archive_path_key(relative)
        if path_key in seen_paths:
            raise A2AFinalizationError("A2A receipt archive contains duplicate paths")
        seen_paths.add(path_key)
        target = _safe_archive_file(archive_root, rendered, label="A2A receipt archive file")
        if sha256_file(target) != row["sha256"]:
            raise A2AFinalizationError("A2A receipt archive file hash does not match")
        if role == "raw_capture":
            raw_capture_digests[rendered] = row["sha256"]
        elif role in paths_by_role:
            raise A2AFinalizationError("A2A receipt archive has duplicate singleton roles")
        else:
            paths_by_role[role] = target
    if set(paths_by_role) != _REQUIRED_ARCHIVE_ROLES or not raw_capture_digests:
        raise A2AFinalizationError("A2A receipt archive is missing required finalizer inputs")
    return archive_root, paths_by_role, raw_capture_digests


def _verify_capture_inventory(
    capture_manifest: dict[str, Any], *, raw_capture_digests: dict[str, str]
) -> None:
    """Require the receipt to bind every, and only, manifest raw capture."""
    captures = capture_manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        raise A2AFinalizationError("A2A receipt capture manifest has no captures")
    expected: dict[str, str] = {}
    for capture in captures:
        if not isinstance(capture, dict):
            raise A2AFinalizationError("A2A receipt capture manifest contains an invalid row")
        relative = _relative_path(
            capture.get("raw_path"), label="A2A receipt capture raw path"
        ).as_posix()
        raw_digest = capture.get("raw_sha256")
        if (
            relative in expected
            or not isinstance(raw_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", raw_digest)
        ):
            raise A2AFinalizationError("A2A receipt capture manifest has invalid raw lineage")
        expected[relative] = raw_digest
    if raw_capture_digests != expected:
        raise A2AFinalizationError(
            "A2A finalization receipt raw-capture inventory does not exactly match its capture manifest"
        )


def verify_finalized_a2a_package_bytes(
    package_bytes: bytes,
    receipt_bytes: bytes,
    *,
    runtime_root: Path,
) -> VerifiedA2AFinalization:
    """Replay immutable finalized packet bytes from the trusted archive."""
    if not isinstance(package_bytes, bytes) or not isinstance(receipt_bytes, bytes):
        raise A2AFinalizationError("A2A finalization replay requires byte snapshots")
    package = _read_object_bytes(package_bytes, label="A2A finalized package")
    receipt = _read_object_bytes(receipt_bytes, label="A2A finalization receipt")
    validate_artifact(package)
    validate_artifact(receipt)
    if receipt.get("contract_id") != FINALIZATION_RECEIPT_CONTRACT:
        raise A2AFinalizationError("expected an A2A finalization receipt contract")
    if receipt.get("status") != "PASS" or receipt.get("finalizer") != {
        "name": FINALIZER_NAME,
        "version": FINALIZER_VERSION,
    }:
        raise A2AFinalizationError("A2A finalization receipt has an unsupported finalizer binding")
    _archive_root, paths_by_role, raw_capture_digests = _verify_receipt_archive(
        receipt, runtime_root=runtime_root
    )
    if (
        sha256_file(paths_by_role["source_bundle"]) != receipt["source_bundle"]["sha256"]
        or sha256_file(paths_by_role["capture_manifest"])
        != receipt["capture"]["capture_manifest_sha256"]
        or sha256_file(paths_by_role["span_ledger"])
        != receipt["capture"]["span_ledger_sha256"]
        or sha256_file(paths_by_role["selection_ledger"])
        != receipt["capture"]["selection_ledger_sha256"]
    ):
        raise A2AFinalizationError("A2A finalization receipt summary does not match archive bytes")
    capture_manifest = _read_object(
        paths_by_role["capture_manifest"], label="A2A receipt capture manifest"
    )
    if capture_manifest.get("capture_id") != receipt["capture"]["capture_id"]:
        raise A2AFinalizationError("A2A finalization receipt has the wrong capture identity")
    _verify_capture_inventory(capture_manifest, raw_capture_digests=raw_capture_digests)
    from tools.prepare_a2a_fact_vector_packet_intake import read_and_verify_bundle

    try:
        verified_bundle = read_and_verify_bundle(paths_by_role["source_bundle"])
    except ValueError as exc:
        raise A2AFinalizationError(
            "A2A receipt source bundle cannot be independently reverified"
        ) from exc
    if (
        verified_bundle.get("bundle_sha256") != receipt["source_bundle"]["sha256"]
        or verified_bundle.get("bundle_manifest_sha256")
        != receipt["source_bundle"]["bundle_manifest_sha256"]
    ):
        raise A2AFinalizationError(
            "A2A finalization receipt source-bundle manifest summary does not match archive bytes"
        )

    package_hash = sha256_bytes(package_bytes)
    matching_records = [
        row
        for row in receipt["packages"]
        if row.get("file_sha256") == package_hash
        and row.get("canonical_digest") == package["canonical_digest"]["value"]
        and row.get("artifact_id") == package.get("artifact_id")
        and row.get("source_id") == package.get("source", {}).get("source_id")
    ]
    if len(matching_records) != 1:
        raise A2AFinalizationError("A2A package bytes are not bound by the finalization receipt")

    from tools.finalize_a2a_hitl_source_packets import finalize_packages

    with tempfile.TemporaryDirectory(prefix="nee-a2a-finalization-replay-") as directory:
        expected_dir = Path(directory) / "expected"
        report = finalize_packages(
            bundle_path=paths_by_role["source_bundle"],
            capture_manifest_path=paths_by_role["capture_manifest"],
            span_ledger_path=paths_by_role["span_ledger"],
            selection_path=paths_by_role["selection_ledger"],
            output_dir=expected_dir,
            audiences=receipt["audiences"],
        )
        regenerated = sorted(
            [
                {
                    "relative_path": row["path"],
                    "file_sha256": sha256_file(expected_dir / row["path"]),
                    "canonical_digest": row["canonical_digest"],
                    "artifact_id": row["artifact_id"],
                    "source_id": row["source_id"],
                }
                for row in report["packages"]
            ],
            key=lambda row: row["relative_path"],
        )
    if regenerated != receipt["packages"]:
        raise A2AFinalizationError("A2A finalization receipt does not match archive replay output")
    if matching_records[0] not in regenerated:
        raise A2AFinalizationError("A2A package was not reproduced from the trusted archive")
    binding = {
        "receipt_id": receipt["receipt_id"],
        "receipt_canonical_digest": receipt["canonical_digest"]["value"],
        "receipt_file_sha256": sha256_bytes(receipt_bytes),
        "package_file_sha256": package_hash,
        "package_canonical_digest": package["canonical_digest"]["value"],
        "archive_root_id": receipt["archive"]["root_id"],
        "archive_binding_digest": digest(receipt["archive"]),
        "verification_state": "finalization_archive_replayed",
    }
    return VerifiedA2AFinalization(
        MappingProxyType(binding),
        _VERIFIER_CAPABILITY,
    )


def verify_finalized_a2a_package(
    package_path: Path,
    receipt_path: Path,
    *,
    runtime_root: Path,
) -> VerifiedA2AFinalization:
    """Read exact package and receipt bytes once, then replay them from archive."""
    try:
        package_bytes = Path(package_path).resolve().read_bytes()
        receipt_bytes = Path(receipt_path).resolve().read_bytes()
    except OSError as exc:
        raise A2AFinalizationError("A2A finalized package or receipt is unavailable") from exc
    return verify_finalized_a2a_package_bytes(
        package_bytes,
        receipt_bytes,
        runtime_root=runtime_root,
    )


def validated_ingress_finalization_reference(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _FINALIZATION_REFERENCE_FIELDS:
        raise A2AFinalizationError("A2A finalization evidence reference has an invalid shape")
    if any(not isinstance(value[key], str) for key in _FINALIZATION_REFERENCE_FIELDS):
        raise A2AFinalizationError("A2A finalization evidence reference has invalid values")
    result: dict[str, str] = {key: value[key] for key in _FINALIZATION_REFERENCE_FIELDS}
    if not _RUN_ID_PATTERN.fullmatch(result["run_id"]):
        raise A2AFinalizationError("A2A finalization evidence reference has an unsafe run ID")
    expected_prefix = f"runs/{result['run_id']}/a2a_finalization/"
    if result["package_relative_path"] != expected_prefix + "finalized_packet.json" or result[
        "receipt_relative_path"
    ] != expected_prefix + "finalization_receipt.json":
        raise A2AFinalizationError("A2A finalization evidence reference is not canonical")
    for field in ("package_file_sha256", "receipt_file_sha256"):
        if not isinstance(result[field], str) or not re.fullmatch(r"[0-9a-f]{64}", result[field]):
            raise A2AFinalizationError("A2A finalization evidence reference has invalid hashes")
    try:
        result["archive_root_id"] = _safe_archive_root_id(result["archive_root_id"])
    except A2AFinalizationError as exc:
        raise A2AFinalizationError(
            "A2A finalization evidence reference has an invalid archive root"
        ) from exc
    return result


def _reverify_ingress_finalization_evidence(
    *,
    runtime_root: Path,
    reference: object,
    expected_binding: object,
) -> tuple[VerifiedA2AFinalization, dict[str, Any]]:
    """Replay a retained packet/receipt and return its exact parsed packet too."""
    reference_value = validated_ingress_finalization_reference(reference)
    if not isinstance(expected_binding, dict) or set(expected_binding) != _FINALIZATION_BINDING_FIELDS:
        raise A2AFinalizationError("A2A finalization audit binding has an invalid shape")
    raw_runtime_root = Path(runtime_root).expanduser().absolute()
    runtime_root = assert_safe_mutable_path(
        raw_runtime_root, raw_runtime_root, label="A2A finalization runtime root"
    )
    package_path = assert_safe_mutable_path(
        runtime_root,
        runtime_root
        / _relative_path(reference_value["package_relative_path"], label="A2A packet snapshot path"),
        label="A2A packet snapshot",
    )
    receipt_path = assert_safe_mutable_path(
        runtime_root,
        runtime_root
        / _relative_path(reference_value["receipt_relative_path"], label="A2A receipt snapshot path"),
        label="A2A receipt snapshot",
    )
    if (
        not package_path.is_file()
        or not receipt_path.is_file()
        or sha256_file(package_path) != reference_value["package_file_sha256"]
        or sha256_file(receipt_path) != reference_value["receipt_file_sha256"]
    ):
        raise A2AFinalizationError("A2A finalization evidence snapshot is missing or altered")
    package_bytes = package_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()
    verification = verify_finalized_a2a_package_bytes(
        package_bytes,
        receipt_bytes,
        runtime_root=runtime_root,
    )
    binding = verified_finalization_binding(verification)
    if (
        binding != expected_binding
        or binding["package_file_sha256"] != reference_value["package_file_sha256"]
        or binding["receipt_file_sha256"] != reference_value["receipt_file_sha256"]
        or binding["archive_root_id"] != reference_value["archive_root_id"]
    ):
        raise A2AFinalizationError("A2A finalization evidence does not match its persisted binding")
    return verification, _read_object_bytes(package_bytes, label="A2A finalized packet snapshot")


def reverify_ingress_finalization_evidence(
    *,
    runtime_root: Path,
    reference: object,
    expected_binding: object,
) -> VerifiedA2AFinalization:
    """Reissue a verifier capability from run-retained exact bytes and archive."""
    verification, _packet = _reverify_ingress_finalization_evidence(
        runtime_root=runtime_root,
        reference=reference,
        expected_binding=expected_binding,
    )
    return verification


def reverify_ingress_finalization_normalized_bundle(
    *,
    runtime_root: Path,
    reference: object,
    expected_binding: object,
) -> tuple[VerifiedA2AFinalization, dict[str, Any]]:
    """Replay then deterministically normalize the retained A2A packet."""
    verification, packet = _reverify_ingress_finalization_evidence(
        runtime_root=runtime_root,
        reference=reference,
        expected_binding=expected_binding,
    )
    from dual_pipeline.normalization import normalize_a2a_fact_vector_packet

    try:
        bundle = normalize_a2a_fact_vector_packet(
            packet,
            finalization_proof=verification,
        )
    except ValueError as exc:
        raise A2AFinalizationError(
            "A2A archived packet cannot be deterministically normalized"
        ) from exc
    return verification, bundle


def a2a_normalized_content_projection(bundle: object) -> dict[str, Any]:
    """Project every source-derived normalized field that may reach GraphDB."""
    if not isinstance(bundle, dict):
        raise A2AFinalizationError("A2A normalized bundle is not an object")
    extensions = bundle.get("extensions")
    if not isinstance(extensions, dict):
        raise A2AFinalizationError("A2A normalized bundle has no extensions")
    context = extensions.get("com.partner_advisory.nee.context")
    source = extensions.get("com.partner_advisory.nee.source")
    governance = extensions.get("com.partner_advisory.nee.a2a-packet-candidate-governance")
    if (
        not isinstance(context, dict)
        or context.get("source_kind") != "a2a_fact_vector_packet"
        or not isinstance(source, dict)
        or not isinstance(governance, dict)
        or not isinstance(bundle.get("source_id"), str)
        or not isinstance(bundle.get("source_version_id"), str)
        or not isinstance(bundle.get("evidence"), list)
        or not isinstance(bundle.get("candidates"), list)
    ):
        raise A2AFinalizationError("A2A normalized bundle has invalid replay content")
    return {
        "source_id": bundle["source_id"],
        "source_version_id": bundle["source_version_id"],
        "evidence": bundle["evidence"],
        "candidates": bundle["candidates"],
        "context": context,
        "source": source,
        "candidate_governance": governance,
    }
