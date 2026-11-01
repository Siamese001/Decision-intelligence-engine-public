"""Validation helpers for BGE-M3 model snapshot manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.core import digest, is_link_or_reparse, read_json
from dual_pipeline.hash_cache import cached_sha256_file


def validate_bge_model_manifest(  # noqa: C901
    model_path: Path, manifest_path: Path, model_revision: str
) -> dict[str, Any]:
    """Verify that a pinned local BGE-M3 snapshot exactly matches its manifest."""

    supplied_model_root = model_path.expanduser()
    supplied_manifest = manifest_path.expanduser()
    if is_link_or_reparse(supplied_model_root):
        raise ValueError("BGE-M3 model root cannot be a link or reparse point")
    if is_link_or_reparse(supplied_manifest):
        raise ValueError("BGE-M3 model manifest cannot be a link or reparse point")
    model_root = supplied_model_root.resolve()
    manifest_file = supplied_manifest.resolve()
    if not model_root.is_dir():
        raise RuntimeError("BGE-M3 model path is not a directory")
    if not manifest_file.is_file():
        raise RuntimeError("BGE-M3 model manifest is not a file")
    try:
        manifest_file.relative_to(model_root)
    except ValueError:
        pass
    else:
        raise ValueError("BGE-M3 model manifest must be outside the immutable snapshot")
    value = read_json(manifest_file)
    if not isinstance(value, dict):
        raise ValueError("BGE-M3 model manifest must be a JSON object")
    declared_digest = value.get("manifest_digest")
    unsigned = {key: item for key, item in value.items() if key != "manifest_digest"}
    actual_digest = digest(unsigned)
    if declared_digest != actual_digest:
        raise ValueError("BGE-M3 model manifest digest mismatch")
    if value.get("schema_version") != "1.0.0" or value.get("model_id") != "BAAI/bge-m3":  # noqa: SSOT_EXEMPT
        raise ValueError("BGE-M3 model manifest identity or schema mismatch")
    if value.get("model_revision") != model_revision:
        raise ValueError("BGE-M3 model manifest revision mismatch")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("BGE-M3 model manifest requires a nonempty file inventory")
    declared_paths: set[str] = set()
    for local_path in model_root.rglob("*"):
        if is_link_or_reparse(local_path):
            raise ValueError(
                "BGE-M3 model snapshot contains a link or reparse point: "
                f"{local_path.relative_to(model_root)}"
            )
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size_bytes", "sha256"}:
            raise ValueError("BGE-M3 model manifest file entries are malformed")
        relative = Path(str(entry["path"]))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in declared_paths
        ):
            raise ValueError("BGE-M3 model manifest contains an unsafe or duplicate path")
        target = model_root / relative
        try:
            target.resolve().relative_to(model_root)
        except ValueError as exc:
            raise ValueError(
                f"BGE-M3 model manifest target escapes the snapshot: {relative}"
            ) from exc
        if not target.is_file():
            raise ValueError(f"BGE-M3 model manifest file is missing: {relative}")
        if target.stat().st_size != entry["size_bytes"] or cached_sha256_file(target) != entry["sha256"]:
            raise ValueError(f"BGE-M3 model file verification failed: {relative}")
        declared_paths.add(relative.as_posix())
    actual_paths = {
        path.relative_to(model_root).as_posix()
        for path in model_root.rglob("*")
        if path.is_file()
    }
    if declared_paths != actual_paths:
        raise ValueError("BGE-M3 model manifest does not exactly cover the local snapshot")
    if "config.json" not in declared_paths or not any(
        path.endswith((".safetensors", ".bin")) for path in declared_paths
    ):
        raise ValueError("BGE-M3 model snapshot lacks config.json or model weights")
    return {
        "status": "PASS",
        "model_id": value["model_id"],
        "model_revision": value["model_revision"],
        "manifest_digest": actual_digest,
        "file_count": len(declared_paths),
        "snapshot_size_bytes": sum(int(entry["size_bytes"]) for entry in files),
    }
