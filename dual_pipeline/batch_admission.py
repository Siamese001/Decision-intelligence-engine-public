"""Exact-source batch admission manifests for scalable governed intake.

The manifest is deliberately a closed set of package file hashes.  A signed
enterprise-production authorization binds the manifest file hash once; every intake and
the eventual batch release then prove membership in that same exact set.  This
replaces the old one-token-per-source-kind-package restriction without turning
an authorization into a broad standing permission.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import create_artifact, validate_artifact
from .core import digest, sha256_file, stable_id, utc_now
from .intake_classifier import sensitive_signal_types

BATCH_MANIFEST_CONTRACT_SUFFIX = ":batch-admission-manifest:1.0.0"
BATCH_MANIFEST_RESOURCE = "intake_batch_manifest"
BATCH_ADMIT_CAPABILITY = "knowledge:batch-admit"
SUPPORTED_SOURCE_KINDS = frozenset(
    {
        "evidence_envelope",
    }
)


def source_kind_for_contract(contract_id: str) -> str:
    suffixes = {
        ":evidence-envelope-intake:1.0.0": "evidence_envelope",
    }
    for suffix, source_kind in suffixes.items():
        if contract_id.endswith(suffix):
            return source_kind
    raise ValueError(
        f"unsupported intake contract {contract_id!r}. All legacy intake mechanisms have been "
        "deleted; the only intake mechanism is the Evidence Envelope JSON format "
        "(urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0)."
    )


def _source_metadata(package: dict[str, Any]) -> tuple[str, list[str], str, str]:
    source = package.get("source")
    if isinstance(source, dict):
        posture = source.get("source_posture")
        location = source.get("source_location")
        classification = source.get("classification")
        audiences = source.get("audiences")
    else:
        meeting = package.get("meeting")
        if not isinstance(meeting, dict):
            raise ValueError("intake package has no source or meeting metadata")
        posture = "meeting_source"
        location = meeting.get("source_location")
        classification = meeting.get("classification")
        audiences = meeting.get("audiences")
    if (
        not isinstance(classification, str)
        or not isinstance(audiences, list)
        or not all(isinstance(value, str) and value for value in audiences)
        or not isinstance(posture, str)
        or not posture
        or not isinstance(location, str)
        or not location
    ):
        raise ValueError("intake package source metadata is incomplete")
    return classification, list(audiences), posture, location


def _package_row(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = Path(path).expanduser().resolve()
    package = json.loads(resolved.read_text(encoding="utf-8"))
    validate_artifact(package)
    classification, audiences, posture, location = _source_metadata(package)
    return package, {
        "artifact_id": package["artifact_id"],
        "contract_id": package["contract_id"],
        "package_file_sha256": sha256_file(resolved),
        "source_kind": source_kind_for_contract(package["contract_id"]),
        "classification": classification,
        "audiences": sorted(audiences),
        "source_posture": posture,
        "source_location": location,
    }


@dataclass(frozen=True)
class BatchAdmissionBinding:
    artifact_id: str
    manifest_file_sha256: str
    manifest_canonical_digest: str
    package_set_digest: str
    policy_id: str
    policy_version: str
    admission_mode: str

    def audit_record(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "manifest_file_sha256": self.manifest_file_sha256,
            "manifest_canonical_digest": self.manifest_canonical_digest,
            "package_set_digest": self.package_set_digest,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "admission_mode": self.admission_mode,
        }


def binding_from_audit_record(value: object) -> BatchAdmissionBinding:
    if not isinstance(value, dict):
        raise ValueError("batch admission audit binding is malformed")
    expected = {
        "artifact_id",
        "manifest_file_sha256",
        "manifest_canonical_digest",
        "package_set_digest",
        "policy_id",
        "policy_version",
        "admission_mode",
    }
    if set(value) != expected or any(
        not isinstance(value[key], str) or not value[key] for key in expected
    ):
        raise ValueError("batch admission audit binding is malformed")
    if any(
        len(value[key]) != 64
        or any(character not in "0123456789abcdef" for character in value[key])
        for key in (
            "manifest_file_sha256",
            "manifest_canonical_digest",
            "package_set_digest",
        )
    ):
        raise ValueError("batch admission audit binding has invalid digests")
    if value["admission_mode"] not in {"HITL_REQUIRED", "SIGNED_POLICY"}:
        raise ValueError("batch admission audit binding has invalid admission mode")
    return BatchAdmissionBinding(**value)


def _validate_policy_shape(manifest: dict[str, Any]) -> None:
    policy = manifest["policy"]
    packages = manifest["packages"]
    if len(packages) > policy["max_package_count"]:
        raise ValueError("batch admission manifest exceeds its package limit")
    package_hashes = [row["package_file_sha256"] for row in packages]
    if len(package_hashes) != len(set(package_hashes)):
        raise ValueError("batch admission manifest has duplicate package hashes")
    classifications = {row["classification"] for row in packages}
    audiences = {tuple(row["audiences"]) for row in packages}
    if classifications != {policy["classification"]}:
        raise ValueError("batch admission manifest mixes classifications")
    if audiences != {tuple(sorted(policy["audiences"]))}:
        raise ValueError("batch admission manifest mixes audiences")
    if any(
        row["source_posture"] not in policy["permitted_source_postures"]
        for row in packages
    ):
        raise ValueError("batch admission manifest contains a disallowed source posture")
    prefixes = policy["permitted_source_location_prefixes"]
    if prefixes and any(
        not any(row["source_location"].startswith(prefix) for prefix in prefixes)
        for row in packages
    ):
        raise ValueError("batch admission manifest contains a disallowed source location")
    if policy["admission_mode"] == "SIGNED_POLICY" and (
        policy["classification"] not in {"public", "internal"}
        or not policy["candidate_only"]
    ):
        raise ValueError(
            "signed batch policy admission is limited to public/internal candidate evidence"
        )


def load_manifest(path: Path) -> tuple[dict[str, Any], BatchAdmissionBinding]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"batch admission manifest does not exist: {resolved}")
    manifest = json.loads(resolved.read_text(encoding="utf-8"))
    validate_artifact(manifest)
    if not manifest["contract_id"].endswith(BATCH_MANIFEST_CONTRACT_SUFFIX):
        raise ValueError("artifact is not a batch admission manifest")
    _validate_policy_shape(manifest)
    binding = BatchAdmissionBinding(
        artifact_id=manifest["artifact_id"],
        manifest_file_sha256=sha256_file(resolved),
        manifest_canonical_digest=manifest["canonical_digest"]["value"],
        package_set_digest=digest(
            sorted(row["package_file_sha256"] for row in manifest["packages"])
        ),
        policy_id=manifest["policy"]["policy_id"],
        policy_version=manifest["policy"]["policy_version"],
        admission_mode=manifest["policy"]["admission_mode"],
    )
    return manifest, binding


def create_manifest(
    *,
    artifact_id: str,
    packages: Iterable[Path],
    policy_id: str,
    policy_version: str,
    admission_mode: str = "HITL_REQUIRED",
    permitted_source_postures: Iterable[str] | None = None,
    permitted_source_location_prefixes: Iterable[str] = (),
    max_package_count: int = 1000,
) -> dict[str, Any]:
    """Build a homogeneous exact-package manifest without retaining raw text."""

    rows: list[dict[str, Any]] = []
    for package_path in packages:
        _package, row = _package_row(package_path)
        rows.append(row)
    if not rows:
        raise ValueError("batch admission manifest requires at least one package")
    classifications = {row["classification"] for row in rows}
    audiences = {tuple(row["audiences"]) for row in rows}
    if len(classifications) != 1:
        raise ValueError("batch admission batches must use one classification")
    if len(audiences) != 1:
        raise ValueError("batch admission batches must use one audience set")
    permitted_postures = sorted(
        set(permitted_source_postures or (row["source_posture"] for row in rows))
    )
    value = create_artifact(
        "batch-admission-manifest",
        artifact_id,
        created_at=utc_now(),
        policy={
            "policy_id": policy_id,
            "policy_version": policy_version,
            "admission_mode": admission_mode,
            "candidate_only": True,
            "classification": classifications.pop(),
            "audiences": list(audiences.pop()),
            "permitted_source_postures": permitted_postures,
            "permitted_source_location_prefixes": sorted(
                set(permitted_source_location_prefixes)
            ),
            "max_package_count": max_package_count,
        },
        packages=sorted(rows, key=lambda row: row["package_file_sha256"]),
        extensions={},
    )
    _validate_policy_shape(value)
    return value


def verify_manifest_packages(
    manifest: dict[str, Any], package_paths: Iterable[Path]
) -> BatchAdmissionBinding:
    """Verify that the manifest contains exactly the supplied package bytes."""

    _validate_policy_shape(manifest)
    package_path_list = [Path(path).expanduser().resolve() for path in package_paths]
    expected = {row["package_file_sha256"]: row for row in manifest["packages"]}
    supplied: dict[str, dict[str, Any]] = {}
    supplied_paths: dict[str, Path] = {}
    for package_path in package_path_list:
        _package, row = _package_row(package_path)
        if row["package_file_sha256"] in supplied:
            raise ValueError("batch admission package paths have duplicate bytes")
        supplied[row["package_file_sha256"]] = row
        supplied_paths[row["package_file_sha256"]] = package_path
    if set(supplied) != set(expected):
        raise PermissionError("batch admission manifest does not match the exact package set")
    for package_hash, row in supplied.items():
        if row != expected[package_hash]:
            raise PermissionError(
                "batch admission manifest metadata does not match package bytes"
            )
        package = json.loads(supplied_paths[package_hash].read_text(encoding="utf-8"))
        if sensitive_signal_types(package):
            raise PermissionError(
                "batch admission policy cannot admit a package with sensitive-content signals"
            )
    return BatchAdmissionBinding(
        artifact_id=manifest["artifact_id"],
        manifest_file_sha256="",
        manifest_canonical_digest=manifest["canonical_digest"]["value"],
        package_set_digest=digest(sorted(expected)),
        policy_id=manifest["policy"]["policy_id"],
        policy_version=manifest["policy"]["policy_version"],
        admission_mode=manifest["policy"]["admission_mode"],
    )


def verify_manifest_package(
    manifest_path: Path, package_path: Path, *, source_kind: str, package: dict[str, Any]
) -> BatchAdmissionBinding:
    """Check one intake package against the immutable batch manifest."""

    manifest, binding = load_manifest(manifest_path)
    if source_kind not in SUPPORTED_SOURCE_KINDS:
        raise ValueError("batch admission manifest has an unsupported source kind")
    _classification, _audiences, _posture, _location = _source_metadata(package)
    actual_hash = sha256_file(Path(package_path).expanduser().resolve())
    candidates = [
        row for row in manifest["packages"] if row["package_file_sha256"] == actual_hash
    ]
    if len(candidates) != 1:
        raise PermissionError("intake package is not admitted by the batch manifest")
    _ignored, actual = _package_row(package_path)
    if actual != candidates[0] or actual["source_kind"] != source_kind:
        raise PermissionError("intake package differs from its batch manifest binding")
    if sensitive_signal_types(package):
        raise PermissionError(
            "batch admission policy cannot admit a package with sensitive-content signals"
        )
    return binding


def verify_candidate_scope(
    manifest_path: Path, bundle: dict[str, Any]
) -> BatchAdmissionBinding:
    """Reject an adapter that broadens a batch policy while normalizing.

    The manifest is intentionally homogeneous.  A package adapter may not
    quietly emit a higher classification or different audience set after the
    source package has been admitted.  Such material must be placed in a new
    exact-scope batch and receive the corresponding admission decision.
    """

    manifest, binding = load_manifest(manifest_path)
    policy = manifest["policy"]
    expected_audiences = sorted(policy["audiences"])
    candidates = bundle.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("normalized batch intake has no candidates")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("normalized batch intake candidate is malformed")
        if candidate.get("classification") != policy["classification"]:
            raise PermissionError(
                "normalized candidate classification exceeds its batch policy"
            )
        audiences = candidate.get("audiences")
        if not isinstance(audiences, list) or sorted(audiences) != expected_audiences:
            raise PermissionError(
                "normalized candidate audiences differ from its batch policy"
            )
    return binding


def policy_approval_id(
    *, directive_id: str, package_set_digest: str, binding: BatchAdmissionBinding
) -> str:
    return stable_id(
        "policy-batch-admission",
        directive_id,
        package_set_digest,
        binding.manifest_file_sha256,
        binding.policy_id,
        binding.policy_version,
        length=28,
    )
