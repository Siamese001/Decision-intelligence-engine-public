"""Canonical JSON serialization, hashing, and file loading primitives."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from .issues import ContractError, fail


def verify_canonical_digest(artifact: dict[str, Any]) -> None:
    declared = artifact.get("canonical_digest")
    if not isinstance(declared, dict):
        fail("DIGEST_MISSING", "$.canonical_digest", "canonical digest object is required")
    if declared.get("algorithm") != "sha256":
        fail("DIGEST_ALGORITHM", "$.canonical_digest.algorithm", "only sha256 is allowed")
    if declared.get("scope") != "artifact_without_canonical_digest":
        fail("DIGEST_SCOPE", "$.canonical_digest.scope", "unexpected digest scope")
    actual = canonical_artifact_digest(artifact)
    if declared.get("value") != actual:
        fail("DIGEST_MISMATCH", "$.canonical_digest.value", f"expected {actual}")

def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_artifact_digest(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.pop("canonical_digest", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot load JSON {path}: {exc}") from exc


def join_path(path: str, token: str | int) -> str:
    return f"{path}[{token}]" if isinstance(token, int) else f"{path}.{token}"

