"""Fail-closed runtime binding checks for query and evaluation execution.

The guard deliberately does not mutate graph or release state.  It only
captures and revalidates the exact inputs that make a result meaningful.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .core import digest, file_lock, sha256_file
from .exceptions import DualPipelineRuntimeError

PAIR_BINDING_FIELDS = (
    "generation_id", "graph_snapshot", "graph_sha256", "sidecar_sha256", "graph_path",
    "sidecar_path", "embedding_backend", "model_id", "model_revision", "model_manifest_digest",
    "production_qualified", "assurance_tier", "internal_operational_policy_digest",
)
BACKEND_BINDING_FIELDS = (
    "backend_id", "model_id", "model_revision", "model_manifest_digest",
    "production_qualified", "assurance_tier",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class DriftError(DualPipelineRuntimeError):
    """A runtime input changed after it was bound to an execution."""

    def __init__(self, code: str, message: str, *, expected: Any = None, observed: Any = None) -> None:
        self.code = code
        self.message = message
        self.expected = expected
        self.observed = observed
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "expected": self.expected, "observed": self.observed}


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DriftError("RUNTIME_BINDING_INVALID", f"{label} must be an object")
    return value


def _require_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DriftError("RUNTIME_BINDING_INVALID", f"{label} must be a lowercase SHA-256 digest", observed=value)
    return value


def pair_binding(pair: Mapping[str, Any]) -> dict[str, Any]:
    """Return the complete pair identity required for an exact execution."""

    from .assurance import pair_assurance_tier

    value = dict(_require_object(dict(pair), label="active pair"))
    if value.get("assurance_tier") is None:
        value["assurance_tier"] = pair_assurance_tier(value)
    if "internal_operational_policy_digest" not in value:
        value["internal_operational_policy_digest"] = None
    missing = [field for field in PAIR_BINDING_FIELDS if field not in value]
    if missing:
        raise DriftError(
            "RUNTIME_BINDING_INVALID",
            "active pair is missing required binding fields",
            observed=missing,
        )
    _require_sha(value["graph_sha256"], label="active pair graph_sha256")
    _require_sha(value["sidecar_sha256"], label="active pair sidecar_sha256")
    _require_sha(value["model_manifest_digest"], label="active pair model_manifest_digest")
    if not isinstance(value["production_qualified"], bool):
        raise DriftError(
            "RUNTIME_BINDING_INVALID",
            "active pair production_qualified must be boolean",
            observed=value["production_qualified"],
        )
    return {field: value[field] for field in PAIR_BINDING_FIELDS}


def backend_binding(backend: Any) -> dict[str, Any]:
    """Capture the backend identity used to encode or retrieve evidence."""

    values = {}
    for field in BACKEND_BINDING_FIELDS:
        value = getattr(backend, field, None)
        if value is None:
            raise DriftError(
                "RUNTIME_BINDING_INVALID",
                f"embedding backend is missing {field}",
            )
        values[field] = value
    _require_sha(
        values["model_manifest_digest"],
        label="embedding backend model_manifest_digest",
    )
    if not isinstance(values["production_qualified"], bool):
        raise DriftError(
            "RUNTIME_BINDING_INVALID",
            "embedding backend production_qualified must be boolean",
            observed=values["production_qualified"],
        )
    return values


def _verify_backend_snapshot(backend: Any) -> None:
    verifier = getattr(backend, "verify_snapshot", None)
    if not callable(verifier):
        return
    try:
        verifier()
    except Exception as exc:
        raise DriftError(
            "BACKEND_BINDING_DRIFT",
            "embedding model snapshot could not be reverified",
        ) from exc


def _assert_pair_matches_backend(
    pair: Mapping[str, Any],
    backend_value: Mapping[str, Any],
) -> None:
    expected = {
        "embedding_backend": backend_value["backend_id"],
        "model_id": backend_value["model_id"],
        "model_revision": backend_value["model_revision"],
        "model_manifest_digest": backend_value["model_manifest_digest"],
        "production_qualified": backend_value["production_qualified"],
        "assurance_tier": backend_value["assurance_tier"],
    }
    from .assurance import pair_assurance_tier

    observed = {
        field: (
            pair_assurance_tier(dict(pair))
            if field == "assurance_tier" and pair.get(field) is None
            else pair.get(field)
        )
        for field in expected
    }
    if expected != observed:
        raise DriftError(
            "BACKEND_BINDING_DRIFT",
            "active pair does not match the embedding backend",
            expected=expected,
            observed=observed,
        )


def _make_binding(
    manager: Any,
    pair: Mapping[str, Any],
    backend_value: Mapping[str, Any],
    policy_digests: Mapping[str, str | None],
) -> dict[str, Any]:
    binding = {
        "runtime_root": str(Path(manager.runtime_root).resolve()),
        "pair": pair_binding(pair),
        "backend": dict(backend_value),
        "policy_digests": dict(sorted(policy_digests.items())),
    }
    binding["binding_digest"] = digest(binding)
    return binding


def _pair_paths(manager: Any, pair: Mapping[str, Any]) -> tuple[Path, Path]:
    try:
        return (
            Path(manager._resolve(str(pair["graph_path"]))),
            Path(manager._resolve(str(pair["sidecar_path"]))),
        )
    except Exception as exc:
        raise DriftError(
            "ACTIVE_PAIR_DRIFT",
            "active pair paths cannot be resolved inside the runtime root",
        ) from exc


def _actual_pair(manager: Any) -> dict[str, Any]:
    try:
        return _require_object(manager.active_pair(), label="active pair")
    except DriftError:
        raise
    except Exception as exc:
        message = str(exc)
        lowered = message.casefold()
        code = (
            "GRAPH_DRIFT"
            if "graph hash" in lowered
            else "SIDECAR_DRIFT"
            if "sidecar hash" in lowered
            else "ACTIVE_PAIR_DRIFT"
        )
        raise DriftError(code, "active pair could not be revalidated", observed=message) from exc


def _assert_pair_files(manager: Any, pair: Mapping[str, Any]) -> None:
    graph_path, sidecar_path = _pair_paths(manager, pair)
    for label, path, expected, code in (
        ("graph", graph_path, pair["graph_sha256"], "GRAPH_DRIFT"),
        ("sidecar", sidecar_path, pair["sidecar_sha256"], "SIDECAR_DRIFT"),
    ):
        try:
            observed = sha256_file(path)
        except Exception as exc:
            raise DriftError(
                code,
                f"active {label} file cannot be read",
                expected=expected,
            ) from exc
        if observed != expected:
            raise DriftError(
                code,
                f"active {label} bytes changed after binding",
                expected=expected,
                observed=observed,
            )


def _compare(
    *,
    code: str,
    label: str,
    expected: Any,
    observed: Any,
) -> None:
    if expected != observed:
        raise DriftError(
            code,
            f"{label} changed after binding",
            expected=expected,
            observed=observed,
        )


def capture_runtime_binding(
    *,
    manager: Any,
    active_pair: Mapping[str, Any],
    backend: Any,
    policy_digests: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Capture and validate the exact runtime inputs for one execution."""

    expected_pair = pair_binding(active_pair)
    policies = dict(sorted((policy_digests or {}).items()))
    with file_lock(manager.lock_path):
        observed_pair = _actual_pair(manager)
        _compare(
            code="ACTIVE_PAIR_DRIFT",
            label="active pair",
            expected=expected_pair,
            observed=pair_binding(observed_pair),
        )
        _assert_pair_files(manager, observed_pair)
        current_backend = backend_binding(backend)
        _verify_backend_snapshot(backend)
        _assert_pair_matches_backend(observed_pair, current_backend)
        return _make_binding(manager, expected_pair, current_backend, policies)


def assert_runtime_binding_unchanged(
    *,
    manager: Any,
    expected_binding: Mapping[str, Any],
    backend: Any,
    policy_digests: Mapping[str, str | None] | None = None,
    stage: str = "terminal",
) -> dict[str, Any]:
    """Re-read and compare every binding component before trusting a result."""

    expected = _require_object(dict(expected_binding), label="runtime binding")
    expected_digest = expected.get("binding_digest")
    expected_without_digest = dict(expected)
    expected_without_digest.pop("binding_digest", None)
    if expected_digest != digest(expected_without_digest):
        raise DriftError(
            "RUNTIME_BINDING_INVALID",
            "runtime binding receipt digest is invalid",
        )
    expected_pair = pair_binding(
        _require_object(expected.get("pair"), label="runtime pair binding")
    )
    expected_backend = _require_object(
        expected.get("backend"), label="runtime backend binding"
    )
    expected_policies = dict(expected.get("policy_digests") or {})
    current_policies = dict(sorted((policy_digests or {}).items()))
    with file_lock(manager.lock_path):
        current_pair = _actual_pair(manager)
        current_pair_binding = pair_binding(current_pair)
        if expected_pair != current_pair_binding:
            differing = {
                field: {
                    "expected": expected_pair.get(field),
                    "observed": current_pair_binding.get(field),
                }
                for field in PAIR_BINDING_FIELDS
                if expected_pair.get(field) != current_pair_binding.get(field)
            }
            code = (
                "GRAPH_DRIFT"
                if differing.get("graph_sha256")
                or differing.get("graph_path")
                else "SIDECAR_DRIFT"
                if differing.get("sidecar_sha256")
                or differing.get("sidecar_path")
                else "ACTIVE_PAIR_DRIFT"
            )
            raise DriftError(code, f"active pair changed during {stage}", observed=differing)
        _assert_pair_files(manager, current_pair)
        _verify_backend_snapshot(backend)
        current_backend = backend_binding(backend)
        if expected_backend != current_backend:
            raise DriftError(
                "BACKEND_BINDING_DRIFT",
                f"embedding backend changed during {stage}",
                expected=expected_backend,
                observed=current_backend,
            )
        _assert_pair_matches_backend(current_pair, current_backend)
        if expected_policies != current_policies:
            raise DriftError(
                "POLICY_DRIFT",
                f"governing policy digests changed during {stage}",
                expected=expected_policies,
                observed=current_policies,
            )
        _compare(
            code="RUNTIME_ROOT_DRIFT",
            label="runtime root",
            expected=expected.get("runtime_root"),
            observed=str(Path(manager.runtime_root).resolve()),
        )
        return _make_binding(manager, expected_pair, current_backend, current_policies)


def assert_replay_binding(
    result: Mapping[str, Any],
    expected_binding: Mapping[str, Any],
) -> None:
    """Reject a cached summary that predates or differs from the current binding."""

    observed = result.get("runtime_binding")
    if not isinstance(observed, dict):
        raise DriftError(
            "REPLAY_BINDING_MISSING",
            "idempotent replay has no runtime binding receipt",
        )
    expected = dict(expected_binding)
    observed_without_digest = dict(observed)
    observed_without_digest.pop("binding_digest", None)
    observed_digest = observed.get("binding_digest")
    if observed_digest != digest(observed_without_digest):
        raise DriftError(
            "REPLAY_BINDING_INVALID",
            "idempotent replay runtime binding digest is invalid",
        )
    _compare(
        code="REPLAY_BINDING_DRIFT",
        label="idempotent replay runtime binding",
        expected=expected,
        observed=observed,
    )


def assert_file_unchanged(path: Path, expected_sha256: str, *, label: str) -> None:
    """Check an input file's bytes without treating a missing file as unchanged."""

    try:
        observed = sha256_file(path)
    except Exception as exc:
        raise DriftError(
            "INPUT_FILE_DRIFT",
            f"{label} cannot be read",
            expected=expected_sha256,
        ) from exc
    if observed != expected_sha256:
        raise DriftError(
            "INPUT_FILE_DRIFT",
            f"{label} changed after binding",
            expected=expected_sha256,
            observed=observed,
        )


__all__ = [
    "BACKEND_BINDING_FIELDS",
    "DriftError",
    "PAIR_BINDING_FIELDS",
    "assert_file_unchanged",
    "assert_replay_binding",
    "assert_runtime_binding_unchanged",
    "backend_binding",
    "capture_runtime_binding",
    "pair_binding",
]
