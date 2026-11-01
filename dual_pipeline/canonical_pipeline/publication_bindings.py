"""Read-only publication input bindings; never load a model or open the graph."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dual_pipeline.core import sha256_bytes


def file_binding(
    path: Path, *, required: bool = False, fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        if required:
            raise FileNotFoundError(f"Missing publication input: {resolved}")
        return {"path": str(resolved), "present": False}
    data = resolved.read_bytes()
    result = {"path": str(resolved), "present": True, "bytes": len(data), "sha256": sha256_bytes(data)}
    if fields:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError(f"Publication context must be a JSON object: {resolved}")
        result["binding"] = {key: value.get(key) for key in fields}
    return result


def canonical_publication_runtime(repository_root: Path) -> Path:
    """Mirror release_runner's runtime selection without creating runtime state."""
    from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
    return resolve_authoritative_runtime_root(repository_root)


def runtime_publication_context(runtime: Path, directive_id: str) -> dict[str, Any]:
    """Snapshots are evidence bindings, not validation or authorization claims."""
    pair = runtime / "active_pair.json"
    directive = runtime / "ingestion_directives" / f"{directive_id}.json"
    return {
        "runtime_root": str(runtime.resolve()),
        "active_pair": file_binding(pair, fields=(
            "generation_id", "graph_sha256", "sidecar_sha256", "graph_path", "sidecar_path",
            "embedding_backend", "model_id", "model_revision", "model_manifest_digest", "production_qualified",
        )),
        "directive": file_binding(directive, fields=(
            "directive_id", "status", "terminal", "approval", "completion", "required_embedding_target", "baseline_pair",
        )),
    }


def publication_bindings(
    package: Path, metadata: dict[str, Any], *, directive_id: str,
    reviewer: str, rationale: str, embedding_backend: str,
    runtime: Path, repository_root: Path, model_path: str, model_manifest: str,
) -> dict[str, Any]:
    """Bind current source, package, release authority and requested backend."""
    package_bytes = package.read_bytes()
    payload = json.loads(package_bytes)
    if not isinstance(payload, dict):
        raise ValueError("Publication package must be a JSON object")
    meeting = payload.get("meeting", {})
    if not isinstance(meeting, dict) or not meeting.get("meeting_id") or not meeting.get("source_hash"):
        raise ValueError("Publication requires exact meeting_id and source_hash bindings")
    out = package.parent
    decision_file = out / "final_release_decision.json"
    judge_file = out / "llm_judge_evaluation.json"
    judge_digest: Optional[str] = None
    if decision_file.is_file() and judge_file.is_file():
        d_val = json.loads(decision_file.read_text(encoding="utf-8"))
        j_val = json.loads(judge_file.read_text(encoding="utf-8"))
        manifest = d_val.get("artifact_manifest", {}) if isinstance(d_val, dict) else {}
        d_digest = manifest.get("judge_evaluation_digest") or d_val.get("judge_evaluation_digest")
        j_digest = j_val.get("evaluation_receipt_digest") if isinstance(j_val, dict) else None
        if not j_digest:
            raise ValueError(f"Missing evaluation_receipt_digest in {judge_file}")
        if not d_digest:
            raise ValueError(f"Missing judge_evaluation_digest in {decision_file}")
        if d_digest != j_digest:
            raise ValueError(f"Publication judge receipt digest mismatch: decision='{d_digest}' != judge='{j_digest}'")
        judge_digest = j_digest

    return {
        "package": {"path": str(package.resolve()), "sha256": sha256_bytes(package_bytes), "bytes": len(package_bytes)},
        "source": {
            "meeting_id": meeting["meeting_id"], "source_hash": meeting["source_hash"],
            "source_location": meeting.get("source_location"),
            "raw_source_sha256": metadata.get("raw_source_sha256"),
        },
        "authority_request": {
            "directive_id": directive_id, "reviewer": reviewer, "rationale": rationale,
            "scope": f"Transcript meeting ingestion: {metadata.get('meeting_name') or metadata.get('title') or 'Meeting'}",
            "release_decision": file_binding(decision_file, required=True),
            "judge_evaluation": file_binding(judge_file, required=True),
            "judge_receipt_digest": judge_digest,
        },
        "access": {
            "classification": meeting.get("classification"), "audiences": meeting.get("audiences"),
            "requested_audiences": ["internal_consulting", "nee-engineering"],
            "signed_authorization_supplied": False,
        },
        "runtime": runtime_publication_context(runtime, directive_id),
        "maintenance_runtime_root": str(runtime.resolve()),
        "graph_override": str(Path(metadata["graph_path"]).resolve()) if metadata.get("graph_path") else None,
        "backend": {
            "requested_backend": embedding_backend, "require_bge_m3": True,
            "allow_dev_bge_m3": embedding_backend == "bge-m3-dev",
            "allow_internal_operational": False, "device": "auto", "fp16": None,
            "model_path": str(Path(model_path).resolve()),
            "model_manifest": file_binding(Path(model_manifest)),
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "model_policy": file_binding(repository_root / "requirements" / "bge-m3-model.json"),
        },
        "publication_settings": {
            "force_rerun": bool(metadata.get("force_rerun")),
            "collision_existing_path": metadata.get("collision_existing_path"),
            "ingestion_reasoning": "off", "offline_ingestion_reasoning": True,
            "reasoning_policy": file_binding(repository_root / "requirements" / "ingestion-reasoning-policy.json"),
        },
    }
