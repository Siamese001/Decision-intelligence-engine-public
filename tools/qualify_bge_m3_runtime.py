#!/usr/bin/env python3
"""Qualify the pinned local BGE-M3 snapshot on CPU without network access."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (  # noqa: E402
    digest,
    read_json,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_exclusive,
)

PIN_CONTRACT = ROOT / "requirements" / "bge-m3-model.json"
DENSE_DIMENSIONS = 1024
DETERMINISM_ABSOLUTE_TOLERANCE = 1e-6
DETERMINISM_RELATIVE_TOLERANCE = 1e-5
REPRESENTATIVE_INPUTS = (
    (
        "approved-priorities",
        "Summarize approved NEE delivery priorities and the evidence supporting them.",
    ),
    (
        "architecture-pdlc",
        "Link architecture components to constraints, owners, decisions, and PDLC actions.",
    ),
)
OFFLINE_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}


class QualificationFailure(RuntimeError):
    """A public, receipt-safe qualification failure."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code


def configure_offline_environment() -> dict[str, str]:
    """Force all supported Hugging Face clients into local-only operation."""

    for name, value in OFFLINE_ENVIRONMENT.items():
        os.environ[name] = value
    return dict(OFFLINE_ENVIRONMENT)


def _load_pin_contract(path: Path) -> dict[str, Any]:
    value = read_json(path.expanduser().resolve())
    expected_keys = {
        "schema_version",
        "model_id",
        "revision",
        "revision_type",
        "source",
        "snapshot_policy",
        "network_policy_at_runtime",
        "expected_dense_dimensions",
        "expected_manifest_digest",
        "expected_snapshot_file_count",
        "expected_snapshot_size_bytes",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise QualificationFailure(
            "model_pin", "MODEL_PIN_INVALID", "BGE-M3 model pin contract is malformed"
        )
    if (
        value["schema_version"] != "1.0.0"
        or value["model_id"] != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
        or value["revision_type"] != "git_commit"
        or value["snapshot_policy"] != "complete"
        or value["network_policy_at_runtime"] != "offline"
        or value["expected_dense_dimensions"] != DENSE_DIMENSIONS
        or not isinstance(value["expected_manifest_digest"], str)
        or len(value["expected_manifest_digest"]) != 64
        or not isinstance(value["expected_snapshot_file_count"], int)
        or value["expected_snapshot_file_count"] < 1
        or not isinstance(value["expected_snapshot_size_bytes"], int)
        or value["expected_snapshot_size_bytes"] < 1
    ):
        raise QualificationFailure(
            "model_pin", "MODEL_PIN_INVALID", "BGE-M3 model pin policy is not supported"
        )
    revision = value["revision"]
    if not isinstance(revision, str) or len(revision) != 40:
        raise QualificationFailure(
            "model_pin", "MODEL_PIN_INVALID", "BGE-M3 model pin is not a full commit revision"
        )
    try:
        int(revision, 16)
        int(value["expected_manifest_digest"], 16)
    except ValueError as exc:
        raise QualificationFailure(
            "model_pin", "MODEL_PIN_INVALID", "BGE-M3 model pin is not hexadecimal"
        ) from exc
    if not str(value["source"]).endswith(revision):
        raise QualificationFailure(
            "model_pin", "MODEL_PIN_INVALID", "BGE-M3 source is not bound to its revision"
        )
    return value


def _fail(stage: str, code: str, message: str) -> None:
    raise QualificationFailure(stage, code, message)


def _validate_runtime_contract() -> dict[str, Any]:
    from dual_pipeline.preflight import (  # imported after offline flags are forced
        check_cpu,
        check_locked_environment,
        check_pip_dependencies,
        check_runtime_contract,
    )

    checks = {
        "runtime_contract": check_runtime_contract(),
        "pip_dependencies": check_pip_dependencies(),
        "locked_environment": check_locked_environment(
            ROOT / "requirements" / "runtime-cpu.lock.txt"
        ),
        "cpu_contract": check_cpu(),
    }
    failed = [name for name, value in checks.items() if value.get("status") != "PASS"]
    if failed:
        _fail(
            "runtime_contract",
            "RUNTIME_CONTRACT_FAILED",
            f"BGE-M3 runtime contract checks failed: {failed}",
        )
    return checks


def _finite_sequence(values: Any, *, stage: str, label: str) -> list[float]:
    if isinstance(values, (str, bytes, dict)):
        _fail(stage, "ENCODING_SHAPE_INVALID", f"{label} is not a numeric sequence")
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise QualificationFailure(
            stage, "ENCODING_VALUE_INVALID", f"{label} contains a nonnumeric value"
        ) from exc
    if not result or any(not math.isfinite(value) for value in result):
        _fail(stage, "ENCODING_VALUE_INVALID", f"{label} is empty or non-finite")
    return result


def _normalize_encoding(value: Any, *, label: str) -> dict[str, Any]:
    stage = f"encode:{label}"
    if not isinstance(value, dict):
        _fail(stage, "ENCODING_SHAPE_INVALID", "BGE-M3 encoding is not an object")
    if set(value) != {"dense", "sparse", "colbert_tokens", "colbert"}:
        _fail(stage, "ENCODING_SHAPE_INVALID", "BGE-M3 encoding fields are incomplete")

    dense = _finite_sequence(value["dense"], stage=stage, label="dense vector")
    if len(dense) != DENSE_DIMENSIONS:
        _fail(
            stage,
            "DENSE_DIMENSIONS_INVALID",
            f"BGE-M3 dense vector has {len(dense)} dimensions, expected {DENSE_DIMENSIONS}",
        )
    if not any(dense):
        _fail(stage, "ENCODING_VALUE_INVALID", "BGE-M3 dense vector is all zero")

    sparse_raw = value["sparse"]
    if not isinstance(sparse_raw, dict) or not sparse_raw:
        _fail(stage, "SPARSE_OUTPUT_EMPTY", "BGE-M3 sparse representation is empty")
    sparse: dict[str, float] = {}
    for key, raw_weight in sparse_raw.items():
        token = str(key)
        if not token or token in sparse:
            _fail(stage, "ENCODING_SHAPE_INVALID", "BGE-M3 sparse keys are invalid")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise QualificationFailure(
                stage, "ENCODING_VALUE_INVALID", "BGE-M3 sparse weight is nonnumeric"
            ) from exc
        if not math.isfinite(weight):
            _fail(stage, "ENCODING_VALUE_INVALID", "BGE-M3 sparse weight is non-finite")
        sparse[token] = weight

    tokens_raw = value["colbert_tokens"]
    colbert_raw = value["colbert"]
    if isinstance(tokens_raw, (str, bytes)) or isinstance(colbert_raw, (str, bytes, dict)):
        _fail(stage, "ENCODING_SHAPE_INVALID", "BGE-M3 ColBERT output is malformed")
    try:
        tokens = [str(token) for token in tokens_raw]
        rows = list(colbert_raw)
    except TypeError as exc:
        raise QualificationFailure(
            stage, "ENCODING_SHAPE_INVALID", "BGE-M3 ColBERT output is not iterable"
        ) from exc
    if not rows or len(tokens) != len(rows):
        _fail(
            stage,
            "COLBERT_SHAPE_INVALID",
            "BGE-M3 ColBERT token and vector cardinalities differ or are empty",
        )
    colbert = [
        _finite_sequence(row, stage=stage, label="ColBERT token vector") for row in rows
    ]
    dimensions = {len(row) for row in colbert}
    if len(dimensions) != 1:
        _fail(stage, "COLBERT_SHAPE_INVALID", "BGE-M3 ColBERT dimensions are inconsistent")

    return {
        "dense": dense,
        "sparse": sparse,
        "colbert_tokens": tokens,
        "colbert": colbert,
    }


def _encoding_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "dense_shape": [len(value["dense"])],
        "sparse_nonzero_count": len(value["sparse"]),
        "colbert_shape": [len(value["colbert"]), len(value["colbert"][0])],
        "representation_digest": digest(value),
    }


def _compare_encodings(first: dict[str, Any], repeat: dict[str, Any]) -> dict[str, Any]:
    if first["sparse"].keys() != repeat["sparse"].keys():
        _fail(
            "determinism",
            "DETERMINISM_SHAPE_MISMATCH",
            "BGE-M3 repeated sparse keys differ",
        )
    if first["colbert_tokens"] != repeat["colbert_tokens"]:
        _fail(
            "determinism",
            "DETERMINISM_SHAPE_MISMATCH",
            "BGE-M3 repeated ColBERT token positions differ",
        )
    if [len(row) for row in first["colbert"]] != [
        len(row) for row in repeat["colbert"]
    ]:
        _fail(
            "determinism",
            "DETERMINISM_SHAPE_MISMATCH",
            "BGE-M3 repeated ColBERT shapes differ",
        )

    pairs: list[tuple[float, float]] = list(zip(first["dense"], repeat["dense"]))
    pairs.extend((first["sparse"][key], repeat["sparse"][key]) for key in first["sparse"])
    pairs.extend(
        zip(
            (item for row in first["colbert"] for item in row),
            (item for row in repeat["colbert"] for item in row),
        )
    )
    deltas = [abs(left - right) for left, right in pairs]
    within_tolerance = all(
        math.isclose(
            left,
            right,
            rel_tol=DETERMINISM_RELATIVE_TOLERANCE,
            abs_tol=DETERMINISM_ABSOLUTE_TOLERANCE,
        )
        for left, right in pairs
    )
    result = {
        "absolute_tolerance": DETERMINISM_ABSOLUTE_TOLERANCE,
        "relative_tolerance": DETERMINISM_RELATIVE_TOLERANCE,
        "maximum_absolute_delta": max(deltas, default=0.0),
        "exact_digest_match": digest(first) == digest(repeat),
        "within_tolerance": within_tolerance,
    }
    if not within_tolerance:
        _fail(
            "determinism",
            "DETERMINISM_TOLERANCE_EXCEEDED",
            "BGE-M3 repeated encoding exceeded the fixed determinism tolerance",
        )
    return result


def _configured_backend_devices(backend: Any) -> list[str]:
    candidates: list[Any] = []
    implementation = getattr(backend, "_model", None)
    for attribute in ("target_devices", "devices"):
        value = getattr(implementation, attribute, None)
        if value is not None:
            if isinstance(value, (str, bytes)):
                candidates.append(value)
            else:
                try:
                    candidates.extend(value)
                except TypeError:
                    candidates.append(value)
    return sorted({str(value) for value in candidates if str(value)})


def _backend_parameter_devices(backend: Any) -> list[str]:
    candidates: list[Any] = []
    implementation = getattr(backend, "_model", None)
    for owner in (getattr(implementation, "model", None), implementation):
        if owner is None:
            continue
        device = getattr(owner, "device", None)
        if device is not None:
            candidates.append(device)
        for accessor_name in ("parameters", "buffers"):
            accessor = getattr(owner, accessor_name, None)
            if callable(accessor):
                for value in accessor():
                    if getattr(value, "device", None) is not None:
                        candidates.append(value.device)
    return sorted({str(value) for value in candidates if str(value)})


def _timed_encode(torch_module: Any, backend: Any, text: str) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    with torch_module.inference_mode():
        value = backend.encode(text)
    return value, round((time.perf_counter() - started) * 1000, 3)


def qualify(
    *,
    model_path: Path,
    model_revision: str,
    model_manifest: Path,
    pin_contract_path: Path = PIN_CONTRACT,
) -> dict[str, Any]:
    """Run the fail-closed runtime qualification and return its PASS receipt."""

    started_at = utc_now()
    started_clock = time.perf_counter()
    stage = "offline_controls"
    offline_controls = configure_offline_environment()
    try:
        stage = "model_pin"
        pin = _load_pin_contract(pin_contract_path)
        if model_revision != pin["revision"]:
            _fail(
                stage,
                "MODEL_REVISION_NOT_PINNED",
                "requested BGE-M3 revision does not equal the repository pin",
            )

        stage = "runtime_contract"
        runtime_contract_checks = _validate_runtime_contract()

        stage = "cpu_runtime"
        torch_module = importlib.import_module("torch")
        cpu_device = torch_module.device("cpu")
        probe = torch_module.arange(1, 5, dtype=torch_module.float32, device=cpu_device)
        if float(probe.sum().item()) != 10.0:
            _fail(
                stage,
                "CPU_TENSOR_PROBE_FAILED",
                "CPU tensor probe returned an unexpected result",
            )

        stage = "backend_import"
        index_module = importlib.import_module("dual_pipeline.index")
        backend_factory = index_module.FlagEmbeddingBackend

        stage = "model_load"
        load_started = time.perf_counter()
        backend = backend_factory(
            str(model_path.expanduser().resolve()),
            model_revision,
            str(model_manifest.expanduser().resolve()),
        )
        initialization_ms = round((time.perf_counter() - load_started) * 1000, 3)
        if (
            backend.backend_id != "flagembedding-bge-m3"
            or backend.model_id != pin["model_id"]
            or backend.model_revision != pin["revision"]
            or not backend.production_qualified
            or not backend.model_manifest_digest
        ):
            _fail(
                stage,
                "BACKEND_IDENTITY_MISMATCH",
                "loaded backend does not match the pinned production BGE-M3 identity",
            )
        configured_devices = _configured_backend_devices(backend)
        if not configured_devices or any(value.casefold() != "cpu" for value in configured_devices):
            _fail(
                stage,
                "MODEL_TARGET_DEVICE_UNVERIFIED",
                "BGE-M3 is not configured exclusively for CPU inference",
            )

        stage = "model_manifest"
        manifest = read_json(model_manifest.expanduser().resolve())
        if (
            not isinstance(manifest, dict)
            or manifest.get("model_revision") != pin["revision"]
            or manifest.get("manifest_digest") != backend.model_manifest_digest
            or manifest.get("manifest_digest") != pin["expected_manifest_digest"]
            or not isinstance(manifest.get("files"), list)
            or not manifest["files"]
            or len(manifest["files"]) != pin["expected_snapshot_file_count"]
            or sum(int(entry["size_bytes"]) for entry in manifest["files"])
            != pin["expected_snapshot_size_bytes"]
        ):
            _fail(
                stage,
                "MODEL_MANIFEST_MISMATCH",
                "verified backend and model manifest identities differ",
            )

        stage = "representative_encodes"
        cases = []
        parameter_devices: list[str] = []
        for label, text in REPRESENTATIVE_INPUTS:
            first_raw, first_ms = _timed_encode(torch_module, backend, text)
            if not parameter_devices:
                parameter_devices = _backend_parameter_devices(backend)
                if not parameter_devices or any(
                    value.casefold() != "cpu" for value in parameter_devices
                ):
                    _fail(
                        stage,
                        "MODEL_DEVICE_UNVERIFIED",
                        "BGE-M3 model parameters are not verifiably resident on CPU",
                    )
            repeat_raw, repeat_ms = _timed_encode(torch_module, backend, text)
            first = _normalize_encoding(first_raw, label=label)
            repeat = _normalize_encoding(repeat_raw, label=f"{label}:repeat")
            comparison = _compare_encodings(first, repeat)
            cases.append(
                {
                    "case_id": label,
                    "input_sha256": sha256_bytes(text.encode("utf-8")),
                    "input_character_count": len(text),
                    "input_utf8_bytes": len(text.encode("utf-8")),
                    "first_encode_latency_ms": first_ms,
                    "repeat_encode_latency_ms": repeat_ms,
                    "first": _encoding_summary(first),
                    "repeat": _encoding_summary(repeat),
                    "determinism": comparison,
                }
            )

        stage = "model_manifest_final"
        final_manifest = index_module.validate_bge_model_manifest(
            model_path.expanduser().resolve(),
            model_manifest.expanduser().resolve(),
            model_revision,
        )
        if (
            final_manifest["manifest_digest"] != pin["expected_manifest_digest"]
            or final_manifest["file_count"] != pin["expected_snapshot_file_count"]
            or final_manifest["snapshot_size_bytes"]
            != pin["expected_snapshot_size_bytes"]
            or final_manifest["manifest_digest"] != backend.model_manifest_digest
        ):
            _fail(
                stage,
                "MODEL_SNAPSHOT_CHANGED",
                "BGE-M3 model snapshot changed during qualification",
            )

        stage = "receipt"
        manifest_files = manifest["files"]
        receipt = {
            "schema_version": "1.0.0",
            "receipt_type": "bge-m3-runtime-qualification",
            "status": "PASS",
            "scope": "model-runtime-only",
            "production_release_authorized": False,
            "started_at": started_at,
            "completed_at": utc_now(),
            "duration_ms": round((time.perf_counter() - started_clock) * 1000, 3),
            "offline_controls": offline_controls,
            "model": {
                "model_id": backend.model_id,
                "model_revision": backend.model_revision,
                "model_manifest_digest": backend.model_manifest_digest,
                "model_manifest_file_sha256": sha256_file(model_manifest.expanduser().resolve()),
                "snapshot_file_count": len(manifest_files),
                "snapshot_size_bytes": sum(int(entry["size_bytes"]) for entry in manifest_files),
                "pin_contract_digest": digest(pin),
            },
            "runtime": {
                "python_implementation": platform.python_implementation(),
                "python_version": platform.python_version(),
                "python_architecture": platform.architecture()[0],
                "torch_version": str(torch_module.__version__),
                "compute_device": str(cpu_device),
                "flagembedding_version": importlib.metadata.version("FlagEmbedding"),
                "backend_id": backend.backend_id,
                "backend_configured_devices": configured_devices,
                "backend_parameter_devices": parameter_devices,
                "contract_checks": runtime_contract_checks,
                "cpu": {
                    "device": str(cpu_device),
                    "thread_count": int(torch_module.get_num_threads()),
                    "tensor_probe": float(probe.sum().item()),
                },
            },
            "qualification": {
                "initialization_latency_ms": initialization_ms,
                "representative_case_count": len(cases),
                "cases": cases,
                "checks": {
                    "offline_environment": "PASS",
                    "immutable_model_pin": "PASS",
                    "cpu_available": "PASS",
                    "complete_model_manifest": "PASS",
                    "backend_identity": "PASS",
                    "model_cpu_residency": "PASS",
                    "dense_sparse_colbert_outputs": "PASS",
                    "repeat_determinism": "PASS",
                    "final_snapshot_match": "PASS",
                },
            },
        }
        receipt["receipt_digest"] = digest(receipt)
        return receipt
    except QualificationFailure:
        raise
    except Exception as exc:
        raise QualificationFailure(
            stage,
            "QUALIFICATION_STAGE_FAILED",
            f"BGE-M3 runtime qualification failed during {stage}: {exc}",
        ) from exc


def failure_receipt(
    *,
    started_at: str,
    model_revision: str,
    failure: QualificationFailure,
) -> dict[str, Any]:
    receipt = {
        "schema_version": "1.0.0",
        "receipt_type": "bge-m3-runtime-qualification",
        "status": "FAIL",
        "scope": "model-runtime-only",
        "production_release_authorized": False,
        "started_at": started_at,
        "completed_at": utc_now(),
        "offline_controls": configure_offline_environment(),
        "model": {
            "model_id": "BAAI/bge-m3",  # noqa: SSOT_EXEMPT
            "requested_model_revision": model_revision,
        },
        "failure": {
            "stage": failure.stage,
            "code": failure.code,
            "message": str(failure),
        },
    }
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    model_root = args.model_path.expanduser().resolve()
    try:
        output.relative_to(model_root)
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the immutable model snapshot")
    if output.exists():
        parser.error("--output already exists; qualification receipts are immutable")
    started_at = utc_now()
    try:
        receipt = qualify(
            model_path=args.model_path,
            model_revision=args.model_revision,
            model_manifest=args.model_manifest,
        )
        exit_code = 0
    except QualificationFailure as exc:
        receipt = failure_receipt(
            started_at=started_at,
            model_revision=args.model_revision,
            failure=exc,
        )
        exit_code = 1
    write_json_exclusive(output, receipt)
    print(
        f"{receipt['status']}: BGE-M3 runtime qualification; "
        f"receipt={output} digest={receipt['receipt_digest']}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
