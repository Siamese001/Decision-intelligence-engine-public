"""Helper functions and resource validators for production preflight."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from config.credentials import CredentialManager

from ..contracts import validate_artifact, validate_file
from ..core import normalize_space, read_json, sha256_bytes, sha256_file
from ..parameters import get_float_parameter
from .models import PreflightConfig


def _dispatch(name: str, fallback: Any) -> Any:
    mod = sys.modules.get("dual_pipeline.preflight")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return fallback


def _pass(**details: Any) -> dict[str, Any]:
    return {"status": "PASS", **details}


def _fail(message: str, **details: Any) -> dict[str, Any]:
    return {"status": "FAIL", "error": message, **details}


def _blocked(*blockers: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "error": "prerequisite checks failed",
        "blockers": list(blockers),
    }


def _safe_check(
    function: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any
) -> dict[str, Any]:
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        return _fail(str(exc), error_type=type(exc).__name__)


@contextmanager
def _offline_environment() -> Iterator[None]:
    names = (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
    )
    with CredentialManager.temporary_env({name: "1" for name in names}):
        yield


def _backend_devices(backend: Any) -> list[str]:
    wrapped = getattr(backend, "_model", None)
    candidates = (getattr(wrapped, "model", None), wrapped)
    devices: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        device = getattr(candidate, "device", None)
        if device is not None:
            devices.add(str(device))
        for accessor_name in ("parameters", "buffers"):
            accessor = getattr(candidate, accessor_name, None)
            if callable(accessor):
                for value in accessor():
                    if getattr(value, "device", None) is not None:
                        devices.add(str(value.device))
    if not devices:
        raise RuntimeError("could not prove that the BGE-M3 model is resident on CPU")
    return sorted(devices)


def _write_probe(directory: Path) -> None:
    probe = directory / f".nee-preflight-write-{uuid.uuid4().hex}.tmp"
    try:
        with probe.open("xb") as stream:
            stream.write(b"preflight")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        probe.unlink(missing_ok=True)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _tracked_paths(repo_root: Path) -> list[Path]:
    timeout = get_float_parameter("timeouts.subprocess_seconds", 120.0)
    completed = subprocess.run(
        ["git", "-C", str(repo_root.resolve()), "ls-files", "-z"],
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError("git ls-files failed; tracked-secret scope cannot be proven")
    return [
        repo_root.resolve() / os.fsdecode(value)
        for value in completed.stdout.split(b"\0")
        if value
    ]


def _qualification_resources(config: PreflightConfig) -> dict[str, str]:
    required_paths = {
        "copilot_package": config.copilot_package,
        "architecture_package": config.architecture_package,
        "pdlc_question": config.pdlc_question_file,
    }
    missing = [name for name, path in required_paths.items() if path is None]
    if not config.approval_id or not config.approval_id.strip():
        missing.append("release_approval")
    if missing:
        raise ValueError(
            f"strict enterprise-production preflight requires resources: {sorted(missing)}"
        )
    for name, path in required_paths.items():
        if path is None or not path.resolve().is_file():
            raise ValueError(f"qualification resource is not a file: {name}")
    question = normalize_space(config.pdlc_question_file.resolve().read_text(encoding="utf-8"))
    if not question:
        raise ValueError("pdlc_question resource is empty")
    resources = {
        "copilot_package": sha256_file(config.copilot_package.resolve()),
        "architecture_package": sha256_file(config.architecture_package.resolve()),
        "pdlc_question": sha256_bytes(question.encode("utf-8")),
        "release_approval": sha256_bytes(config.approval_id.encode("utf-8")),
    }
    for name, path in (
        ("copilot_decisions", config.copilot_decisions),
        ("architecture_decisions", config.architecture_decisions),
    ):
        if path is not None:
            if not path.resolve().is_file():
                raise ValueError(f"qualification resource is not a file: {name}")
            resources[name] = sha256_file(path.resolve())
    return resources


def _validate_qualification_resource_contracts(config: PreflightConfig) -> dict[str, str]:
    expected = {
        "copilot_package": (
            config.copilot_package,
            "urn:nee:dual-pipeline:copilot-intake:1.0.0",
        ),
        "architecture_package": (
            config.architecture_package,
            "urn:nee:dual-pipeline:architecture-diagram-intake:1.0.0",
        ),
    }
    validated: dict[str, str] = {}
    file_validator = _dispatch("validate_file", validate_file)
    for name, (path, contract_id) in expected.items():
        if path is None:
            raise ValueError(f"qualification resource is missing: {name}")
        try:
            value = file_validator(path.resolve())
        except Exception as exc:
            raise ValueError(
                f"qualification resource contract validation failed: {name}"
            ) from exc
        if value.get("contract_id") != contract_id:
            raise ValueError(f"qualification resource has the wrong contract: {name}")
        validated[name] = contract_id
    for name, path in (
        ("copilot_decisions", config.copilot_decisions),
        ("architecture_decisions", config.architecture_decisions),
    ):
        if path is None:
            continue
        try:
            value = read_json(path.resolve())
        except Exception as exc:
            raise ValueError(f"qualification decision resource is unreadable: {name}") from exc
        decisions = value if isinstance(value, list) else value.get("decisions", [])
        if not isinstance(decisions, list) or not decisions:
            raise ValueError(f"qualification decision resource is empty or malformed: {name}")
        for decision in decisions:
            try:
                validate_artifact(decision)
            except Exception as exc:
                raise ValueError(
                    f"qualification decision contract validation failed: {name}"
                ) from exc
            if decision.get("contract_id") != "urn:nee:dual-pipeline:hitl-decision:1.0.0":
                raise ValueError(f"qualification resource has the wrong contract: {name}")
        validated[name] = "urn:nee:dual-pipeline:hitl-decision-list:1.0.0"
    validated["pdlc_question"] = "normalized-nonempty-utf8"
    validated["release_approval"] = "nonempty-identifier"
    return validated
