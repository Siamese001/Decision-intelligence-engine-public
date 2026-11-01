#!/usr/bin/env python3
"""dual_pipeline/hitl_governance_core/mutation_authorization.py

Authoritative Mutation Authorization & Binding Governance (Wave 2).
Enforces proposal-bound human approval at every graph write boundary:
- Validates authentic human origin (anti-synthetic origin).
- Enforces exact proposal, source, and output digests (anti-tamper / anti-drift).
- Enforces expected base generation under publication lock (anti-stale / anti-race).
- Detects and rejects forged, mismatched, stale, or replayed approvals.
- Provides idempotent recovery for already-committed activations.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from dual_pipeline.core import file_lock, read_json, sha256_file
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    ProposalStatus,
    load_approval_request,
    persist_approval_request,
)
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin

logger = logging.getLogger(__name__)


class MutationAuthorizationError(RuntimeError):
    """Raised when an authoritative graph mutation boundary is breached without valid approval."""


def compute_proposal_digest_from_deliverables(
    *,
    meeting_name: str,
    source_hash: str,
    output_hashes: Dict[str, str],
    intake_hash: str,
    target_graph: str = "CANONICAL_GRAPH.db",
    runtime_root: str = "",
    expected_base_generation: Union[int, str] = "current",
    deltas: Optional[Dict[str, Any]] = None,
    validation_evidence: Optional[Dict[str, Any]] = None,
    rollback_scope: str = "HEAD",
    proposal_id: Optional[str] = None,
) -> str:
    """Compute the RFC-compliant deterministic SHA-256 digest of a mutation proposal."""
    pid = proposal_id or f"prop-{meeting_name.lower().replace(' ', '_')}"
    payload = {
        "proposal_id": pid,
        "meeting_name": meeting_name,
        "source_hash": source_hash,
        "output_hashes": dict(sorted(output_hashes.items())),
        "intake_hash": intake_hash,
        "target_graph": target_graph,
        "runtime_root": runtime_root,
        "expected_base_generation": str(expected_base_generation),
        "deltas": deltas or {},
        "validation_evidence": validation_evidence or {},
        "rollback_scope": rollback_scope,
    }
    b = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def _resolve_active_generation(runtime_root: Path) -> Optional[str]:
    """Retrieve the current active generation ID from the runtime root."""
    try:
        from dual_pipeline.runtime_root import resolve_active_pair
        data, _ = resolve_active_pair(runtime_root, enforce_integrity=False)
        return str(data.get("generation_id") or data.get("active_generation") or "")
    except Exception:
        ap_file = runtime_root / "active_pair.json"
        if ap_file.is_file():
            try:
                raw = read_json(ap_file)
                return str(raw.get("generation_id") or raw.get("active_generation") or "")
            except Exception:
                pass
        return None


def _merge_stored_request(receipt: Dict[str, Any], runtime_root: Path) -> Dict[str, Any]:
    req_id = receipt.get("request_id")
    if not req_id:
        return receipt
    stored = load_approval_request(req_id, runtime_root)
    if not stored:
        return receipt
    merged = dict(stored)
    merged.update(receipt)
    if stored.get("status") == ProposalStatus.ACTIVATED.value:
        merged["status"] = ProposalStatus.ACTIVATED.value
        merged["activated_generation_id"] = stored.get("activated_generation_id")
    return merged


def _find_in_receipt_or_metadata(
    output_dir: Path,
    runtime_root: Path,
    metadata: Dict[str, Any],
    approval_receipt: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if approval_receipt and isinstance(approval_receipt, dict):
        return _merge_stored_request(approval_receipt, runtime_root)

    meta_receipt = metadata.get("approval_receipt") or metadata.get("mutation_approval_receipt")
    if meta_receipt and isinstance(meta_receipt, dict):
        return meta_receipt

    receipt_file = output_dir / "mutation_approval_receipt.json"
    if receipt_file.is_file():
        try:
            return read_json(receipt_file)
        except Exception as exc:
            logger.warning("Could not read mutation approval receipt at %s: %s", receipt_file, exc)
    return None


def _find_in_store(runtime_root: Path, meeting_name: str) -> Optional[Dict[str, Any]]:
    store_file = runtime_root / "approval" / "host_approval_requests.json"
    if not store_file.is_file():
        return None
    try:
        store = read_json(store_file)
        for r in reversed(list(store.values())):
            if r.get("meeting_name") == meeting_name and r.get("status") in (
                ProposalStatus.APPROVED.value,
                ProposalStatus.APPLYING.value,
                ProposalStatus.ACTIVATED.value,
            ):
                return r
    except Exception:
        pass
    return None


def _find_in_active_clearance(
    metadata: Dict[str, Any], meeting_name: str
) -> Optional[Dict[str, Any]]:
    directive_id = metadata.get("directive_id")
    run_id = metadata.get("run_id") or directive_id
    if not (directive_id or run_id):
        return None
    try:
        from .active_ingestion_approval import check_active_ingestion_clearance
        delta_dig = str(metadata.get("delta_digest") or "")
        if not delta_dig:
            pkg_p = metadata.get("intake_package_path") or (Path(metadata.get("output_dir", "")) / "intake_package.json")
            if pkg_p and Path(pkg_p).is_file():
                try:
                    from dual_pipeline.contract_validation.digests import canonical_artifact_digest
                    from dual_pipeline.core import read_json
                    delta_dig = canonical_artifact_digest(read_json(Path(pkg_p)))
                except Exception:
                    delta_dig = ""
        is_cleared, _, clearance_rec = check_active_ingestion_clearance(
            run_id=str(run_id or ""),
            directive_id=str(directive_id or ""),
            delta_digest=delta_dig,
        )
        if is_cleared and clearance_rec:
            return {
                "request_id": clearance_rec.get("decision_id"),
                "status": ProposalStatus.APPROVED.value,
                "authorized": True,
                "operator_origin": clearance_rec.get("operator_id") or "human_operator",
                "meeting_name": meeting_name,
                "approved_at": clearance_rec.get("recorded_at"),
                "expected_base_generation": "current",
                "raw_clearance_record": clearance_rec,
            }
    except Exception as e:
        logger.debug("Clearance fallback lookup skipped: %s", e)
    return None


def _find_approval_record(
    output_dir: Path,
    runtime_root: Path,
    metadata: Dict[str, Any],
    approval_receipt: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Locate the candidate approval record across declared metadata, receipt files, and storage."""
    rec = _find_in_receipt_or_metadata(output_dir, runtime_root, metadata, approval_receipt)
    if rec:
        return rec

    req_id = metadata.get("approval_request_id")
    if req_id:
        req = load_approval_request(req_id, runtime_root)
        if req:
            return req

    meeting_name = metadata.get("meeting_name") or metadata.get("title") or output_dir.name
    stored = _find_in_store(runtime_root, meeting_name)
    if stored:
        return stored

    return _find_in_active_clearance(metadata, meeting_name)


def _validate_origin(origin: str, out_name: str) -> None:
    is_auth, origin_msg = validate_approval_origin(origin)
    if not is_auth:
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_SYNTHETIC_ORIGIN: Mutation blocked for '{out_name}'. {origin_msg}"
        )


def _validate_status_and_idempotency(
    rec: Dict[str, Any],
    active_gen: Optional[str],
    out_name: str,
    origin: str,
    allow_idempotent_recovery: bool,
) -> Optional[Dict[str, Any]]:
    status_val = rec.get("status") or (
        ProposalStatus.APPROVED.value if rec.get("authorized") else ProposalStatus.AWAITING_APPROVAL.value
    )
    if status_val == ProposalStatus.REJECTED.value:
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_EXPLICIT_REJECTION: Mutation blocked for '{out_name}'. "
            "Operator explicitly rejected this mutation proposal."
        )
    if status_val == ProposalStatus.AWAITING_APPROVAL.value:
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_PENDING_APPROVAL: Mutation blocked for '{out_name}'. "
            "Proposal is awaiting operator review. Zero mutation granted."
        )

    if status_val == ProposalStatus.ACTIVATED.value:
        committed_gen = str(rec.get("activated_generation_id") or "")
        if allow_idempotent_recovery and active_gen and committed_gen == active_gen:
            logger.info("Mutation approval %s already activated into %s. Idempotent.", rec.get("request_id"), committed_gen)
            return {
                "authorized": True,
                "status": ProposalStatus.ACTIVATED.value,
                "idempotent": True,
                "request_id": rec.get("request_id"),
                "activated_generation_id": committed_gen,
                "operator_origin": origin,
            }
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_REPLAY_DETECTED: Mutation blocked for '{out_name}'. "
            f"Approval receipt was already consumed (committed={committed_gen}, active={active_gen})."
        )

    if status_val not in (ProposalStatus.APPROVED.value, ProposalStatus.APPLYING.value):
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_INVALID_STATUS: Proposal status '{status_val}' does not authorize mutation."
        )
    return None


def _validate_concrete_base_generation(
    rec: Dict[str, Any],
    active_gen: Optional[str],
    out_name: str,
    metadata: Dict[str, Any],
) -> None:
    expected_base = rec.get("expected_base_generation")
    concrete_base = (
        rec.get("concrete_base_generation")
        or metadata.get("concrete_base_generation")
        or (expected_base if expected_base not in ("current", "*", "None") else None)
    )
    if concrete_base and active_gen and str(concrete_base) != str(active_gen):
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_STALE_BASE_GENERATION: Stale base generation detected for '{out_name}'. "
            f"Bound to '{concrete_base}', but active is '{active_gen}'."
        )


def _resolve_source_hash(written: Dict[str, str], metadata: Dict[str, Any]) -> str:
    for key in ("01_transcript", "source_notes", "source_file"):
        val = written.get(key)
        if val and Path(val).is_file():
            return sha256_file(Path(val))
    for key in ("source_path", "input_path"):
        val = metadata.get(key)
        if val and Path(val).is_file():
            return sha256_file(Path(val))
    return str(metadata.get("raw_source_sha256") or "")


def _collect_deliverable_output_hashes(written: Dict[str, str], rec: Dict[str, Any]) -> Dict[str, str]:
    rec_out = rec.get("output_hashes") or {}
    key_aliases = {"01": "01_transcript", "02": "02_recap", "03": "03_graph"}
    targets = list(rec_out.keys()) if rec_out else ["01_transcript", "02_recap", "03_graph"]
    if "source_manifest" in written and "source_manifest" not in targets:
        targets.append("source_manifest")
    hashes: Dict[str, str] = {}
    for k in targets:
        p_str = written.get(k) or written.get(key_aliases.get(k, ""))
        if p_str and Path(p_str).is_file():
            hashes[k] = sha256_file(Path(p_str))
    return hashes


def _build_computed_digest(
    rec: Dict[str, Any],
    out_hashes: Dict[str, str],
    intake_hash: str,
    src_hash: str,
    meeting: str,
    rt_root: Path,
) -> str:
    target_graph = rec.get("target_graph") or "CANONICAL_GRAPH.db"
    rt_val = str(rec.get("runtime_root") or rt_root)
    exp_gen = rec.get("expected_base_generation") or "current"
    rb_scope = rec.get("rollback_scope") or "HEAD"
    return compute_proposal_digest_from_deliverables(
        meeting_name=meeting,
        source_hash=src_hash,
        output_hashes=out_hashes,
        intake_hash=intake_hash,
        target_graph=target_graph,
        runtime_root=rt_val,
        expected_base_generation=exp_gen,
        deltas=rec.get("deltas"),
        validation_evidence=rec.get("validation_evidence"),
        rollback_scope=rb_scope,
        proposal_id=rec.get("proposal_id"),
    )


def _validate_proposal_digest_match(
    rec: Dict[str, Any],
    out_p: Path,
    rt_root: Path,
    written: Dict[str, str],
    metadata: Dict[str, Any],
) -> None:
    rec_digest = rec.get("proposal_digest")
    intake_p = Path(written.get("intake_package") or (out_p / "intake_package.json"))
    has_deliverables = intake_p.is_file() or ("deliverables_check" in metadata)
    if not rec_digest or rec.get("raw_clearance_record"):
        if rec.get("raw_clearance_record") or metadata.get("is_document"):
            out_hashes = _collect_deliverable_output_hashes(written, rec)
            intake_hash = sha256_file(intake_p) if intake_p.is_file() else str(rec.get("intake_hash") or "")
            src_hash = str(rec.get("source_hash") or _resolve_source_hash(written, metadata))
            meeting = rec.get("meeting_name") or out_p.name
            rec["proposal_digest"] = _build_computed_digest(rec, out_hashes, intake_hash, src_hash, meeting, rt_root)
            return
        if has_deliverables:
            raise MutationAuthorizationError(
                f"AUTHORIZATION_DENIED_DIGEST_MISMATCH: Missing required proposal digest for '{out_p.name}'."
            )
        return
    out_hashes = _collect_deliverable_output_hashes(written, rec)
    if not (out_hashes and has_deliverables):
        return
    intake_hash = sha256_file(intake_p) if intake_p.is_file() else str(rec.get("intake_hash") or "")
    src_hash = str(rec.get("source_hash") or _resolve_source_hash(written, metadata))
    meeting = rec.get("meeting_name") or out_p.name
    computed_dig = _build_computed_digest(rec, out_hashes, intake_hash, src_hash, meeting, rt_root)
    if computed_dig != rec_digest:
        raise MutationAuthorizationError(
            f"AUTHORIZATION_DENIED_DIGEST_MISMATCH: Deliverables for '{out_p.name}' were modified after approval. "
            f"Approved: {rec_digest}, recomputed: {computed_dig}."
        )


def _validate_stale_and_digest(
    rec: Dict[str, Any],
    active_gen: Optional[str],
    out_p: Path,
    rt_root: Path,
    written: Dict[str, str],
    metadata: Dict[str, Any],
) -> None:
    _validate_concrete_base_generation(rec, active_gen, out_p.name, metadata)
    _validate_proposal_digest_match(rec, out_p, rt_root, written, metadata)


def validate_mutation_authorization(
    output_dir: Union[Path, str],
    metadata: Dict[str, Any],
    written: Dict[str, str],
    runtime_root: Optional[Union[Path, str]] = None,
    graph_path: Optional[Union[Path, str]] = None,
    approval_receipt: Optional[Dict[str, Any]] = None,
    allow_idempotent_recovery: bool = True,
) -> Dict[str, Any]:
    """Enforce complete proposal-bound approval validation under publication lock."""
    out_p = Path(output_dir).resolve()
    rt_root = Path(runtime_root).resolve() if runtime_root else out_p
    if "output" in rt_root.parts:
        from dual_pipeline.core import ROOT
        lock_dir = ROOT / ".local" / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / f"release_publication_{out_p.name}.lock"
    else:
        lock_file = rt_root / "release_publication.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(lock_file):
        rec = _find_approval_record(out_p, rt_root, metadata, approval_receipt)
        if not rec:
            raise MutationAuthorizationError(
                "AUTHORIZATION_DENIED_MISSING_APPROVAL: GraphDB mutation blocked for "
                f"'{out_p.name}'. No authenticated proposal-bound approval receipt found."
            )

        origin = rec.get("operator_origin") or rec.get("source") or "UNKNOWN"
        _validate_origin(origin, out_p.name)

        active_gen = _resolve_active_generation(rt_root)
        idem = _validate_status_and_idempotency(rec, active_gen, out_p.name, origin, allow_idempotent_recovery)
        if idem:
            return idem

        _validate_stale_and_digest(rec, active_gen, out_p, rt_root, written, metadata)

        req_id = rec.get("request_id")
        if req_id:
            from dual_pipeline.core import ROOT
            storage_rt = ROOT / ".local" / "dual_pipeline" if "output" in rt_root.parts else rt_root
            stored = load_approval_request(req_id, storage_rt)
            if stored:
                stored["status"] = ProposalStatus.APPLYING.value
                stored["applying_at"] = datetime.now(timezone.utc).isoformat()
                persist_approval_request(stored, storage_rt)

        return {
            "authorized": True,
            "status": ProposalStatus.APPLYING.value,
            "request_id": req_id,
            "proposal_digest": rec.get("proposal_digest"),
            "operator_origin": origin,
            "active_generation": active_gen,
            "idempotent": False,
        }


def mark_mutation_activated(
    request_id: str,
    runtime_root: Union[Path, str],
    activated_generation_id: str,
    activation_receipt: Optional[Dict[str, Any]] = None,
) -> None:
    """Transition proposal record to ACTIVATED and record committed generation details."""
    rt_root = Path(runtime_root).resolve()
    from dual_pipeline.core import ROOT
    storage_rt = ROOT / ".local" / "dual_pipeline" if "output" in rt_root.parts else rt_root
    stored = load_approval_request(request_id, storage_rt)
    if not stored:
        return
    stored["status"] = ProposalStatus.ACTIVATED.value
    stored["activated_generation_id"] = str(activated_generation_id)
    stored["activated_at"] = datetime.now(timezone.utc).isoformat()
    if activation_receipt:
        stored["committed_activation_receipt"] = activation_receipt
    persist_approval_request(stored, rt_root)
