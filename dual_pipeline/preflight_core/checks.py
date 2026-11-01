"""Individual system, environment, security, and model checks for preflight."""
from __future__ import annotations

import importlib.metadata
import math
import platform
import re
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from packaging.utils import canonicalize_name

from ..authorization import load_production_authorization_context
from ..core import (
    read_json,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
)
from ..index import (
    DENSE_DIMENSIONS,
    get_backend,
    is_link_or_reparse,
    validate_bge_model_manifest,
)
from .constants import (
    _SECRET_PATTERNS,
    MODEL_PIN_CONTRACT,
    PACKAGE_MANIFEST,
    PIP_CHECK_TIMEOUT_SECONDS,
    RELEASE_STATE,
    REQUIRED_QUALIFICATION_CAPABILITIES,
    RUNTIME_CONTRACT,
    SMOKE_TEXT,
)
from .helpers import (
    _backend_devices,
    _dispatch,
    _fail,
    _offline_environment,
    _pass,
    _qualification_resources,
    _tracked_paths,
    _validate_qualification_resource_contracts,
    _within,
    _write_probe,
)
from .models import PreflightConfig


def check_runtime_contract(contract_path: Path = RUNTIME_CONTRACT) -> dict[str, Any]:
    """Verify CPython and each direct distribution against the tracked contract."""

    contract = read_json(contract_path.resolve())
    python_contract = contract["python"]
    issues: list[str] = []
    if platform.python_implementation() != python_contract["implementation"]:
        issues.append("Python implementation mismatch")
    if sys.version_info[:2] != (python_contract["major"], python_contract["minor"]):
        issues.append("Python major/minor version mismatch")
    architecture = f"{struct.calcsize('P') * 8}bit"
    if architecture != python_contract["architecture"]:
        issues.append("Python architecture mismatch")

    installed: dict[str, str | None] = {}
    for distribution, expected in sorted(contract["distributions"].items()):
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        installed[distribution] = actual
        if actual != expected:
            issues.append(f"{distribution} must be {expected}; installed={actual or 'missing'}")
    details = {
        "contract_sha256": sha256_file(contract_path.resolve()),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "architecture": architecture,
        },
        "direct_dependencies": installed,
    }
    return _fail("; ".join(issues), **details) if issues else _pass(**details)


def check_pip_dependencies(
    *, timeout_seconds: float = PIP_CHECK_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Run a bounded installed-distribution consistency check.

    A timer owns termination independently of ``communicate()``.  This keeps a
    wedged pip child from holding a BGE release before any staged graph work or
    retrieval evaluation has begun.
    """

    if not 1 <= timeout_seconds <= 300:
        raise ValueError("pip check timeout must be between 1 and 300 seconds")
    command = [sys.executable, "-m", "pip", "check"]
    sub = _dispatch("subprocess", subprocess)
    process = sub.Popen(
        command,
        stdout=sub.PIPE,
        stderr=sub.PIPE,
        text=True,
    )
    timed_out = threading.Event()

    def terminate_if_running() -> None:
        if process.poll() is None:
            timed_out.set()
            process.kill()

    watchdog = threading.Timer(timeout_seconds, terminate_if_running)
    watchdog.daemon = True
    watchdog.start()
    try:
        stdout, stderr = process.communicate()
    finally:
        watchdog.cancel()
    if process.poll() is None:
        timed_out.set()
        process.kill()
    output = "\n".join(
        part.strip() for part in (stdout, stderr) if part.strip()
    )
    details = {
        "command": [Path(sys.executable).name, "-m", "pip", "check"],
        "exit_code": process.returncode,
        "summary": output[:4000],
        "timeout_seconds": timeout_seconds,
    }
    if timed_out.is_set():
        return _fail(
            "pip check timed out",
            reason_code="PIP_CHECK_TIMEOUT",
            **details,
        )
    return _pass(**details) if process.returncode == 0 else _fail("pip check failed", **details)


def check_locked_environment(lock_path: Path) -> dict[str, Any]:
    """Require the installed distribution closure to equal the hash-locked runtime."""

    lock_path = lock_path.resolve()
    expected: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if match:
            expected[canonicalize_name(match.group(1))] = match.group(2)
    if not expected:
        return _fail("runtime lock contains no pinned distributions")
    installed = {
        canonicalize_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    allowed_bootstrap = {"pip"}
    missing = sorted(set(expected) - set(installed))
    extra = sorted(set(installed) - set(expected) - allowed_bootstrap)
    mismatches = {
        name: {"expected": expected[name], "installed": installed.get(name)}
        for name in sorted(set(expected) & set(installed))
        if installed[name] != expected[name]
    }
    details = {
        "lock_sha256": sha256_file(lock_path),
        "expected_distribution_count": len(expected),
        "installed_distribution_count": len(installed),
        "allowed_bootstrap_distributions": sorted(allowed_bootstrap & set(installed)),
        "missing": missing,
        "extra": extra,
        "version_mismatches": mismatches,
    }
    if missing or extra or mismatches:
        return _fail("installed distributions do not exactly match the runtime lock", **details)
    return _pass(**details)


def check_cpu(contract_path: Path = RUNTIME_CONTRACT) -> dict[str, Any]:
    """Require the contracted CPU runtime and prove a CPU tensor operation."""

    import torch

    contract = read_json(contract_path.resolve())
    compute = contract["compute"]
    if (
        compute.get("platform") != "cpu"
        or compute.get("wheel_channel") != "cpu"
        or compute.get("device") != "cpu"
    ):
        return _fail(
            "runtime contract does not authorize CPU execution",
            platform=compute.get("platform"),
            wheel_channel=compute.get("wheel_channel"),
            device=compute.get("device"),
        )
    device = torch.device("cpu")
    probe = torch.arange(1, 5, dtype=torch.float32, device=device)
    probe_sum = float(probe.sum().item())
    if probe_sum != 10.0:
        return _fail("CPU tensor probe returned an unexpected result", tensor_probe=probe_sum)
    return _pass(
        available=True,
        device=str(device),
        torch_version=str(torch.__version__),
        tensor_probe=probe_sum,
        thread_count=int(torch.get_num_threads()),
    )


def check_model_manifest(
    model_path: Path,
    model_manifest: Path,
    model_revision: str,
    pin_contract_path: Path = MODEL_PIN_CONTRACT,
) -> dict[str, Any]:
    """Verify the complete local snapshot inventory and immutable revision."""

    pin = read_json(pin_contract_path.resolve())
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
    if not isinstance(pin, dict) or set(pin) != expected_keys:
        raise ValueError("BGE-M3 model pin contract is malformed")
    if (
        pin["schema_version"] != "1.0.0"
        or pin["model_id"] != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
        or pin["revision_type"] != "git_commit"
        or pin["snapshot_policy"] != "complete"
        or pin["network_policy_at_runtime"] != "offline"
        or pin["expected_dense_dimensions"] != DENSE_DIMENSIONS
        or not isinstance(pin["expected_manifest_digest"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", pin["expected_manifest_digest"])
        or not isinstance(pin["expected_snapshot_file_count"], int)
        or pin["expected_snapshot_file_count"] < 1
        or not isinstance(pin["expected_snapshot_size_bytes"], int)
        or pin["expected_snapshot_size_bytes"] < 1
        or not isinstance(pin["revision"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", pin["revision"])
        or not str(pin["source"]).endswith(pin["revision"])
    ):
        raise ValueError("BGE-M3 model pin contract is invalid")
    if model_revision != pin["revision"]:
        raise ValueError("requested BGE-M3 revision does not match the tracked immutable pin")
    manifest_validator = _dispatch("validate_bge_model_manifest", validate_bge_model_manifest)
    result = manifest_validator(model_path, model_manifest, model_revision)
    manifest = read_json(model_manifest.resolve())
    snapshot_size_bytes = sum(int(entry["size_bytes"]) for entry in manifest["files"])
    if (
        result["manifest_digest"] != pin["expected_manifest_digest"]
        or result["file_count"] != pin["expected_snapshot_file_count"]
        or snapshot_size_bytes != pin["expected_snapshot_size_bytes"]
    ):
        raise ValueError("BGE-M3 snapshot does not match the tracked official inventory")
    return _pass(
        model_id=result["model_id"],
        model_revision=result["model_revision"],
        model_manifest_digest=result["manifest_digest"],
        file_count=result["file_count"],
        snapshot_size_bytes=snapshot_size_bytes,
        model_pin_contract_sha256=sha256_file(pin_contract_path.resolve()),
    )


def check_offline_bge_m3_smoke(
    model_path: Path,
    model_manifest: Path,
    model_revision: str,
    *,
    backend_factory: Callable[..., Any] = get_backend,
) -> dict[str, Any]:
    """Load the real backend locally and verify all three BGE-M3 representations."""

    import torch

    started = time.perf_counter()
    with _offline_environment():
        backend = backend_factory(
            "bge-m3",
            model_path=str(model_path.resolve()),
            model_revision=model_revision,
            model_manifest=str(model_manifest.resolve()),
        )
        if not backend.production_qualified or backend.model_id != "BAAI/bge-m3":  # noqa: SSOT_EXEMPT
            raise RuntimeError("smoke probe did not load the production BGE-M3 backend")
        encoded = backend.encode(SMOKE_TEXT)
        devices = _backend_devices(backend)
        if any(device.casefold() != "cpu" for device in devices):
            raise RuntimeError(f"BGE-M3 model has non-CPU parameters or buffers: {devices}")

    dense = encoded.get("dense")
    sparse = encoded.get("sparse")
    colbert = encoded.get("colbert")
    if (
        not isinstance(dense, list)
        or len(dense) != DENSE_DIMENSIONS
        or not all(math.isfinite(float(value)) for value in dense)
        or not any(float(value) != 0.0 for value in dense)
    ):
        raise RuntimeError("BGE-M3 dense smoke output is invalid")
    if (
        not isinstance(sparse, dict)
        or not sparse
        or not all(math.isfinite(float(value)) for value in sparse.values())
    ):
        raise RuntimeError("BGE-M3 sparse smoke output is invalid")
    if not isinstance(colbert, list) or not colbert:
        raise RuntimeError("BGE-M3 ColBERT smoke output is empty")
    dimensions: set[int] = set()
    for vector in colbert:
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("BGE-M3 ColBERT smoke output contains an invalid row")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "BGE-M3 ColBERT smoke output contains a nonnumeric value"
            ) from exc
        if any(not math.isfinite(value) for value in values):
            raise RuntimeError("BGE-M3 ColBERT smoke output contains a non-finite value")
        dimensions.add(len(values))
    if len(dimensions) != 1:
        raise RuntimeError("BGE-M3 ColBERT smoke output is invalid or inconsistent")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return _pass(
        offline_environment={
            "HF_HUB_OFFLINE": True,
            "TRANSFORMERS_OFFLINE": True,
            "HF_DATASETS_OFFLINE": True,
        },
        backend_id=backend.backend_id,
        model_id=backend.model_id,
        model_revision=backend.model_revision,
        model_manifest_digest=backend.model_manifest_digest,
        model_device=devices[0],
        model_devices=devices,
        input_sha256=sha256_bytes(SMOKE_TEXT.encode("utf-8")),
        input_character_count=len(SMOKE_TEXT),
        dense_dimensions=len(dense),
        sparse_weight_count=len(sparse),
        colbert_token_count=len(colbert),
        colbert_dimensions=dimensions.pop(),
        torch_version=str(torch.__version__),
        torch_thread_count=int(torch.get_num_threads()),
        elapsed_ms=elapsed_ms,
    )


def load_seed_contract(repo_root: Path) -> dict[str, Any]:
    release_state_path = repo_root.resolve() / RELEASE_STATE.name
    manifest_path = repo_root.resolve() / PACKAGE_MANIFEST.name
    release_state = read_json(release_state_path)
    package_manifest = read_json(manifest_path)
    release_hash = release_state.get("graph_sha256")
    manifest_hash = package_manifest.get("baseline", {}).get("database_sha256")
    if not isinstance(release_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", release_hash):
        raise ValueError("release_state.json does not declare a valid graph SHA-256")
    if manifest_hash != release_hash:
        raise ValueError("release_state.json and manifest.json disagree on the seed graph hash")
    return _pass(
        expected_sha256=release_hash,
        release_state_sha256=sha256_file(release_state_path),
        package_manifest_sha256=sha256_file(manifest_path),
    )


def check_seed_graph(graph: Path, expected_sha256: str) -> dict[str, Any]:
    graph = graph.resolve()
    if not graph.is_file():
        return _fail("seed graph is not a file")
    actual_sha256 = sha256_file(graph)
    integrity = sqlite_integrity(graph)
    details = {
        "sha256": actual_sha256,
        "expected_sha256": expected_sha256,
        "integrity_check": integrity["integrity_check"],
        "foreign_key_violation_count": integrity["foreign_key_violation_count"],
    }
    if actual_sha256 != expected_sha256:
        return _fail("seed graph hash does not match the approved release contract", **details)
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
        return _fail("seed graph SQLite integrity check failed", **details)
    return _pass(**details)


def check_runtime_root(runtime_root: Path) -> dict[str, Any]:
    runtime_root = runtime_root.resolve()
    existed = runtime_root.exists()
    if existed and not runtime_root.is_dir():
        return _fail("runtime root exists but is not a directory")
    if existed and any(runtime_root.iterdir()):
        return _fail("runtime root must be new or empty")
    runtime_root.mkdir(parents=True, exist_ok=True)
    _write_probe(runtime_root)
    if any(runtime_root.iterdir()):
        return _fail("runtime root write probe did not leave the directory empty")
    return _pass(path=str(runtime_root), was_new=not existed, empty=True, writable=True)


def check_report_destination(report: Path, runtime_root: Path) -> dict[str, Any]:
    report = report.resolve()
    runtime_root = runtime_root.resolve()
    try:
        report.relative_to(runtime_root)
    except ValueError:
        pass
    else:
        return _fail("report destination must be outside the qualification runtime root")
    if report.exists() and report.is_dir():
        return _fail("report destination is a directory")
    if report.exists():
        return _fail("report destination already exists; preflight receipts are immutable")
    report.parent.mkdir(parents=True, exist_ok=True)
    _write_probe(report.parent)
    return _pass(path=str(report), writable=True)


def check_path_separation(config: PreflightConfig) -> dict[str, Any]:
    """Keep mutable runtime/evidence paths outside immutable model and graph inputs."""

    runtime = config.runtime_root.resolve()
    report = config.report.resolve()
    model = config.model_path.resolve()
    manifest = config.model_manifest.resolve()
    graph = config.graph.resolve()
    issues: list[str] = []
    if _within(runtime, model) or _within(model, runtime):
        issues.append("runtime root and model snapshot overlap")
    if _within(report, model):
        issues.append("report is inside the model snapshot")
    if _within(manifest, model):
        issues.append("model manifest is inside the model snapshot")
    if _within(graph, runtime):
        issues.append("seed graph is inside the mutable runtime root")
    if report in {graph, manifest}:
        issues.append("report aliases an immutable input")
    for label, path in (
        ("runtime root", config.runtime_root.expanduser()),
        ("report", config.report.expanduser()),
    ):
        if path.exists() and is_link_or_reparse(path):
            issues.append(f"{label} is a link or reparse point")
    details = {
        "runtime_root": str(runtime),
        "report": str(report),
        "model_path": str(model),
        "model_manifest": str(manifest),
        "graph": str(graph),
    }
    return _fail("; ".join(issues), **details) if issues else _pass(**details)


def check_tracked_secrets(
    repo_root: Path, *, tracked_paths: list[Path] | None = None
) -> dict[str, Any]:
    """Scan current contents of every Git-tracked regular file without echoing matches."""

    repo_root = repo_root.resolve()
    paths = _tracked_paths(repo_root) if tracked_paths is None else tracked_paths
    findings: list[dict[str, str]] = []
    scanned = 0
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise RuntimeError("git returned a tracked path outside the repository") from exc
        if not resolved.is_file():
            continue
        content = resolved.read_bytes()
        scanned += 1
        for secret_type, pattern in _SECRET_PATTERNS:
            if pattern.search(content):
                findings.append({"path": relative, "secret_type": secret_type})
    details = {
        "tracked_file_count": len(paths),
        "scanned_file_count": scanned,
        "findings": findings,
    }
    return _fail("tracked secret candidates detected", **details) if findings else _pass(**details)


def check_production_authorization(
    config: PreflightConfig, model_manifest_digest: str
) -> dict[str, Any]:
    auth_values = (
        config.auth_token,
        config.auth_public_key,
        config.auth_issuer,
        config.auth_audience,
    )
    resource_values = (
        config.copilot_package,
        config.architecture_package,
        config.pdlc_question_file,
        config.approval_id,
        config.copilot_decisions,
        config.architecture_decisions,
    )
    requested = config.strict_production or any(auth_values) or any(resource_values)
    if not requested:
        return {
            "status": "SKIP",
            "reason": (
                "authorization was not requested; infrastructure-only receipt cannot "
                "authorize production"
            ),
        }
    if not all(auth_values):
        return _fail(
            "all four enterprise-production authorization arguments are required together"
        )
    resources = _qualification_resources(config)
    resource_contracts = _validate_qualification_resource_contracts(config)
    loader = _dispatch("load_production_authorization_context", load_production_authorization_context)
    context = loader(
        config.auth_token.resolve(),
        config.auth_public_key.resolve(),
        supplied_issuer=config.auth_issuer,
        supplied_audience=config.auth_audience,
        required_capabilities=REQUIRED_QUALIFICATION_CAPABILITIES,
    )
    context.require_resources(resources)
    if context.approved_model_manifest_digest != model_manifest_digest:
        raise PermissionError(
            "authorization token is not bound to the verified BGE-M3 model manifest"
        )
    return _pass(
        authorization=context.audit_record(),
        qualification_resource_digests=dict(sorted(resources.items())),
        qualification_resource_contracts=dict(sorted(resource_contracts.items())),
    )
