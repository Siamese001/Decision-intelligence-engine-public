"""Governed single-document and jointly admitted document-batch publication."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.core import ROOT, sha256_file
from dual_pipeline.credentials import CredentialManager

logger = logging.getLogger(__name__)
_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
_PERMITTED_BATCH_ROLES = {"table", "email", "document", "deck", "spec"}


def _publish_args(
    packages: list[Path], *, directive_id: str, scope: str,
    reviewer: str, rationale: str, embedding_backend: str,
    batch_manifest: Path | None = None,
    audience: str = "internal_consulting,nee-engineering",
) -> argparse.Namespace:
    from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
    from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
        resolve_active_graph_path,
    )

    auth_runtime_root = resolve_authoritative_runtime_root(ROOT)
    active_graph = resolve_active_graph_path(ROOT) or (ROOT / "graph" / "nee_graph.sqlite")
    return argparse.Namespace(
        package=packages, directive_id=directive_id, scope=scope,
        reviewer=reviewer, rationale=rationale, embedding_backend=embedding_backend,
        allow_dev_bge_m3=(embedding_backend == "bge-m3-dev"),
        allow_internal_operational=False, internal_operational_policy=None,
        model_path=str(ROOT / "models" / "bge-m3" / _MODEL_REVISION),
        model_manifest=str(ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{_MODEL_REVISION}.json"),
        model_revision=_MODEL_REVISION, runtime_root=auth_runtime_root,
        graph=active_graph, batch_manifest=batch_manifest,
        a2a_finalization_receipt=None, retrieval_release_suite=None,
        require_bge_m3=True, auth_token=None, auth_public_key=None,
        auth_issuer=None, auth_audience=None, embedding_device="auto",
        embedding_fp16=None, audience=audience,
        semantic_cache_mode=None, ingestion_reasoning="off",
        ingestion_reasoning_policy=ROOT / "requirements" / "ingestion-reasoning-policy.json",
        offline_ingestion_reasoning=True,
    )


def publish_document_to_graph(
    intake_pkg_path: Path, slug: str, date_dirname: str, metadata: Dict[str, Any],
    directive_id: Optional[str] = None, reviewer: Optional[str] = None,
    rationale: Optional[str] = None, embedding_backend: str = "bge-m3",
) -> Dict[str, Any]:
    """Publish one sealed document via the existing guarded candidate release."""
    from dual_pipeline.cli.handlers import _intake_publish
    forged_keys = {"reviewer_authority", "elevation", "bypass_rbac", "override_role"}
    if any(k in metadata for k in forged_keys):
        raise ValueError(
            "Unauthorized authority field detected in document metadata. "
            "Document body headings/signatures cannot elevate reviewer authority."
        )

    # Forged signatures or extracted document headings cannot elevate reviewer authority.
    # Reviewer must come from explicit caller argument or authenticated environment, never document body metadata.
    effective_reviewer = reviewer or CredentialManager.get_credential("NEE_REVIEWER") or "Operator"
    dir_id = directive_id or f"DIR-DOC-{slug[:25]}-{date_dirname}"
    raw_aud = metadata.get("audiences") or metadata.get("audience")
    if isinstance(raw_aud, list) and raw_aud:
        resolved_aud = ",".join(str(a).strip() for a in raw_aud if str(a).strip())
    elif isinstance(raw_aud, str) and raw_aud.strip():
        resolved_aud = raw_aud.strip()
    else:
        resolved_aud = "internal_consulting,nee-engineering"
    args = _publish_args(
        [Path(intake_pkg_path)], directive_id=dir_id,
        scope=f"Document ingestion: {metadata.get('title', slug)}",
        reviewer=effective_reviewer,
        rationale=rationale or f"Document Ingestion: {slug}",
        embedding_backend=embedding_backend,
        audience=resolved_aud,
    )
    logger.info("Dispatching document package to _intake_publish (directive=%s)", dir_id)
    return _intake_publish(args)


def _validate_package_roles(roles: list[str]) -> None:
    if any(role not in _PERMITTED_BATCH_ROLES for role in roles):
        raise ValueError("Joint document publication contains an unsupported package role")
    if set(roles) <= {"table", "email"}:
        if roles[0] != "table":
            raise ValueError("Joint document publication must start with a table")
        if "email" not in roles or any(role == "table" for role in roles[roles.index("email") + 1:]):
            raise ValueError("Email dependencies must follow all table packages")
    elif "email" in roles:
        if "table" not in roles or roles.index("email") < roles.index("table"):
            raise ValueError("Joint document publication must start with a table")
        if any(role == "table" for role in roles[roles.index("email") + 1:]):
            raise ValueError("Email dependencies must follow all table packages")


def _extract_verified_meeting_ids(paths: list[Path]) -> list[str]:
    from dual_pipeline.document_pipeline_core.retrieval_visibility import preflight_document_clearance
    meeting_ids = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        meeting = payload.get("meeting") or {}
        preflight_document_clearance(meeting, required=True)
        identity = meeting.get("meeting_id")
        if not isinstance(identity, str) or not identity or not meeting.get("source_hash"):
            raise ValueError("Joint package requires distinct meeting and source identities")
        meeting_ids.append(identity)
    if len(set(meeting_ids)) != len(meeting_ids):
        raise ValueError("Joint packages cannot reuse a source identity")
    return meeting_ids


def _validate_dependencies(dependencies: dict[str, str], meeting_ids: list[str], roles: list[str]) -> None:
    table_ids = {identity for identity, role in zip(meeting_ids, roles) if role == "table"}
    email_ids = {identity for identity, role in zip(meeting_ids, roles) if role == "email"}
    if email_ids or set(roles) <= {"table", "email"}:
        if set(dependencies) != email_ids or any(parent not in table_ids for parent in dependencies.values()):
            raise ValueError("Each email must declare an existing table dependency")
    else:
        valid_ids = set(meeting_ids)
        if any(child not in valid_ids or parent not in valid_ids or child == parent for child, parent in dependencies.items()):
            raise ValueError("Batch dependencies must reference valid distinct package identities")


def publish_document_batch_to_graph(
    packages: list[tuple[Path, str]], *, directive_id: str,
    batch_manifest: Path, reviewer: str, rationale: str,
    dependencies: dict[str, str], embedding_backend: str = "bge-m3",
) -> Dict[str, Any]:
    """Send an exact, jointly admitted table→email set through prepare_many once.

    The existing intake guard authenticates admission and verifies the sealed
    manifest. This boundary checks source identity and order before it can call
    that guard; it never relabels content or creates its own approval.
    """
    from dual_pipeline.batch_admission import load_manifest
    from dual_pipeline.cli.handlers import _intake_publish
    if not directive_id or not reviewer or not rationale:
        raise ValueError("Joint publication requires an explicit directive, reviewer and rationale")
    if len(packages) < 2 or len(packages) != len({Path(path).resolve() for path, _ in packages}):
        raise ValueError("Joint publication requires at least two distinct packages")
    roles = [role for _, role in packages]
    _validate_package_roles(roles)
    paths = [Path(path).resolve(strict=True) for path, _ in packages]
    manifest, _ = load_manifest(batch_manifest)
    declared = {row["package_file_sha256"] for row in manifest["packages"]}
    if declared != {sha256_file(path) for path in paths} or len(declared) != len(paths):
        raise ValueError("Joint admission manifest does not bind the exact package set")
    meeting_ids = _extract_verified_meeting_ids(paths)
    _validate_dependencies(dependencies, meeting_ids, roles)
    args = _publish_args(
        paths, directive_id=directive_id, scope="Joint document ingestion: " + ", ".join(meeting_ids),
        reviewer=reviewer, rationale=rationale, embedding_backend=embedding_backend,
        batch_manifest=Path(batch_manifest).resolve(),
    )
    result = _intake_publish(args)
    result["source_order"] = meeting_ids
    result["source_dependencies"] = dict(dependencies)
    return result
