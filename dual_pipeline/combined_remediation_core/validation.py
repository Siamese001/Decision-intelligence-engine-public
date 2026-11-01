"""Validation and verification functions for combined remediation controls."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import validate_artifact
from ..core import digest
from ..project_status_reclassification import (
    validate_project_status_reclassification_control,
)
from ..source_retirement import (
    validate_source_retirement_control,
)
from .constants import (
    _PAIR_FIELDS,
    _REQUIRED_CONTROL_KEYS,
    COMBINED_REMEDIATION_CONTROL_KEY,
    COMBINED_REMEDIATION_POLICY_VERSION,
)


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"combined remediation {label} must be nonblank text")
    return value.strip()


def _require_sha256(value: object, *, label: str) -> str:
    result = _require_text(value, label=label)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"combined remediation {label} must be lowercase SHA-256")
    return result


def _pair_binding(pair: dict[str, Any], *, include_runtime_root: bool) -> dict[str, Any]:
    fields = _PAIR_FIELDS if include_runtime_root else _PAIR_FIELDS[1:]
    return {field: pair[field] for field in fields} | {
        "production_qualified": pair["production_qualified"]
    }


def validate_combined_remediation_control(control: object) -> dict[str, Any]:
    """Validate the whole signed remediation scope before any graph operation."""
    if not isinstance(control, dict) or set(control) != _REQUIRED_CONTROL_KEYS:
        raise ValueError("combined remediation control has an invalid shape")
    supplied_digest = _require_sha256(control["canonical_digest"], label="canonical_digest")
    unsigned = dict(control)
    unsigned.pop("canonical_digest")
    if supplied_digest != digest(unsigned):
        raise ValueError("combined remediation control digest mismatch")
    if control["schema_version"] != "1.0.0":
        raise ValueError("combined remediation control has an unsupported version")
    if control["control_type"] != "combined_remediation":
        raise ValueError("combined remediation control must declare combined_remediation type")
    _require_text(control["reason"], label="reason")
    base_pair = control["base_active_pair"]
    if not isinstance(base_pair, dict):
        raise ValueError("combined remediation base_active_pair must be an object")
    for field in _PAIR_FIELDS:
        _require_text(base_pair.get(field), label=f"base_active_pair.{field}")
    _require_sha256(base_pair["graph_sha256"], label="base_active_pair.graph_sha256")
    _require_sha256(base_pair["sidecar_sha256"], label="base_active_pair.sidecar_sha256")
    _require_sha256(
        base_pair["model_manifest_digest"],
        label="base_active_pair.model_manifest_digest",
    )
    if not isinstance(base_pair.get("production_qualified"), bool):
        raise ValueError("base_active_pair.production_qualified must be a boolean")
    order = control["source_retirement_execution_order"]
    if not isinstance(order, list) or not order:
        raise ValueError("combined remediation execution order must be a nonempty list")
    if len(order) != len(set(order)):
        raise ValueError("combined remediation execution order contains duplicates")
    retirement_controls = control["source_retirement_controls"]
    if not isinstance(retirement_controls, list) or len(retirement_controls) != len(order):
        raise ValueError("combined remediation source retirement controls mismatch execution order")
    normalized_retirements: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for expected_source, retirement_item in zip(order, retirement_controls, strict=True):
        validated_item = validate_source_retirement_control(retirement_item)
        if validated_item["target_source_id"] != expected_source:
            raise ValueError("source retirement control target does not match execution order")
        if validated_item["target_source_id"] in seen_sources:
            raise ValueError("duplicate source retirement target")
        seen_sources.add(validated_item["target_source_id"])
        normalized_retirements.append(validated_item)
    status_control = validate_project_status_reclassification_control(
        control["project_status_reclassification_control"]
    )
    return {
        "schema_version": "1.0.0",
        "control_type": "combined_remediation",
        "base_active_pair": {
            "runtime_root": str(base_pair["runtime_root"]),
            "generation_id": str(base_pair["generation_id"]),
            "graph_sha256": base_pair["graph_sha256"],
            "sidecar_sha256": base_pair["sidecar_sha256"],
            "embedding_backend": str(base_pair["embedding_backend"]),
            "model_id": str(base_pair["model_id"]),
            "model_revision": str(base_pair["model_revision"]),
            "model_manifest_digest": base_pair["model_manifest_digest"],
            "production_qualified": bool(base_pair["production_qualified"]),
        },
        "source_retirement_execution_order": list(order),
        "source_retirement_controls": normalized_retirements,
        "project_status_reclassification_control": status_control,
        "reason": control["reason"].strip(),
        "canonical_digest": supplied_digest,
    }


def combined_remediation_control_from_package(package: object) -> dict[str, Any]:
    """Extract the sealed remediation extension from a Markdown source package."""
    if not isinstance(package, dict):
        raise ValueError("combined remediation control package must be an object")
    validate_artifact(package)
    if not str(package.get("contract_id", "")).endswith(":markdown-source-intake:1.0.0"):
        raise ValueError("combined remediation control must use the Markdown source adapter")
    extensions = package.get("extensions")
    if not isinstance(extensions, dict):
        raise ValueError("combined remediation control package has no extensions")
    envelope = extensions.get(COMBINED_REMEDIATION_CONTROL_KEY)
    if not isinstance(envelope, dict) or set(envelope) != {
        "control_file_sha256",
        "control_canonical_digest",
        "control",
    }:
        raise ValueError("combined remediation package has an invalid control extension")
    _require_sha256(envelope["control_file_sha256"], label="package.control_file_sha256")
    control = validate_combined_remediation_control(envelope["control"])
    if envelope["control_canonical_digest"] != control["canonical_digest"]:
        raise RuntimeError("combined remediation package lost its control digest binding")
    return control


def _assert_active_binding(
    control: dict[str, Any], *, active_pair: dict[str, Any], runtime_root: Path | None = None
) -> None:
    expected = control["base_active_pair"]
    actual = _pair_binding(active_pair, include_runtime_root=False)
    expected_without_root = _pair_binding(expected, include_runtime_root=False)
    if actual != expected_without_root:
        raise RuntimeError("combined remediation active pair drifted from its sealed control")
    if runtime_root is not None and Path(expected["runtime_root"]).resolve() != Path(runtime_root).resolve():
        raise RuntimeError("combined remediation runtime root drifted from its sealed control")


def _assert_plan_digest(plan: dict[str, Any]) -> None:
    expected = dict(plan)
    supplied = expected.pop("plan_digest", None)
    if supplied != digest(expected):
        raise RuntimeError("combined remediation plan digest mismatch")
    if plan.get("plan_type") != "combined_remediation":
        raise ValueError("unsupported combined remediation plan")
    if plan.get("policy_version") != COMBINED_REMEDIATION_POLICY_VERSION:
        raise ValueError("unsupported combined remediation policy version")
    control = validate_combined_remediation_control(plan.get("control"))
    if control != plan["control"] or plan.get("control_digest") != control["canonical_digest"]:
        raise ValueError("combined remediation plan has an invalid control binding")
    _require_text(plan.get("approval_id"), label="plan.approval_id")
    _require_text(plan.get("run_id"), label="plan.run_id")
    _require_text(plan.get("event_timestamp"), label="plan.event_timestamp")
    if not isinstance(plan.get("components"), list) or not plan["components"]:
        raise ValueError("combined remediation plan has no components")


def _aggregate_counts(items: list[dict[str, int]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            result[key] = result.get(key, 0) + int(value)
    return dict(sorted(result.items()))
