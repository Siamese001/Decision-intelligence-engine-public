"""Active Ingestion Approval Request Orchestration, Persistence, and Resumption."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from dual_pipeline.core import digest, read_json, write_json
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    dispatch_interactive_question,
    validate_approval_response_signature,
)


def _seal_contract(payload: Dict[str, Any]) -> Dict[str, Any]:
    copy_payload = dict(payload)
    copy_payload.pop("canonical_digest", None)
    copy_payload["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(copy_payload),
    }
    return copy_payload


def build_active_ingestion_approval_request(
    *,
    run_id: str,
    directive_id: str,
    runtime_root: str,
    project: str,
    namespace: str = "production",
    source_package_digest: str,
    proposed_delta_digest: str,
    baseline_generation: int,
    permitted_operation_class: str = "candidate_publication",
    table_deltas: Optional[Dict[str, int]] = None,
    summary_context: Optional[str] = None,
    package_path: Optional[str] = None,
    ttl_hours: int = 24,
    intake_package_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a governed active-ingestion-approval-request artifact."""
    if not table_deltas:
        raise ValueError("table_deltas must be explicitly provided and non-empty (no fallback or placeholder allowed).")
    clean_src = str(source_package_digest or "").strip()
    if not clean_src or set(clean_src.replace("sha256:", "").replace("0x", "")) <= {"0"}:
        raise ValueError(f"source_package_digest must be a valid non-zero digest (got {source_package_digest!r}).")
    clean_delta = str(proposed_delta_digest or "").strip()
    if not clean_delta or set(clean_delta.replace("sha256:", "").replace("0x", "")) <= {"0"}:
        raise ValueError(f"proposed_delta_digest must be a valid non-zero digest (got {proposed_delta_digest!r}).")
    now = dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(hours=ttl_hours)
    req_seed = f"{run_id}:{directive_id}:{proposed_delta_digest}"
    req_hash = hashlib.sha256(req_seed.encode("utf-8")).hexdigest()[:16]
    request_id = f"req-ingest-{req_hash}"

    deltas = dict(table_deltas)
    context = summary_context or f"Active ingestion proposes graph mutation under directive {directive_id}."

    options = [
        {
            "option_id": "opt_approve",
            "key": "opt_approve",
            "label": f"Approve {permitted_operation_class.replace('_', ' ')} into active knowledge graph",
            "confidence_tier": "HIGH",
            "confidence_score": 0.95,
            "confidence": 0.95,
            "is_recommended": True,
            "recommended": True,
            "action": "Proceed with release preparation and activation",
        },
        {
            "option_id": "opt_decline",
            "key": "opt_decline",
            "label": f"Decline {permitted_operation_class.replace('_', ' ')}",
            "confidence_tier": "LOW",
            "confidence_score": 0.05,
            "confidence": 0.05,
            "is_recommended": False,
            "recommended": False,
            "action": "Halt ingestion workflow and record decline without mutating graph",
        },
    ]

    try:
        from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore

        store = HITLDecisionStore(repo_root=runtime_root)
        options = store.calibrate_options(options, category="INGESTION_MUTATION")
        for opt in options:
            if "confidence_score" in opt:
                score = float(opt["confidence_score"])
                opt["confidence_tier"] = "HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW")
                opt["is_recommended"] = bool(opt.get("recommended", opt.get("is_recommended", False)))
    except Exception:
        pass

    payload: Dict[str, Any] = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
        "schema_version": "1.0.0",
        "artifact_id": f"aiar-{request_id}",
        "request_id": request_id,
        "run_id": run_id,
        "directive_id": directive_id,
        "runtime_root": runtime_root,
        "package_path": str(package_path) if package_path else None,
        "project": project,
        "namespace": namespace,
        "source_package_digest": source_package_digest,
        "proposed_delta_digest": proposed_delta_digest,
        "baseline_generation": baseline_generation,
        "permitted_operation_class": permitted_operation_class,
        "expires_at": expires.isoformat(),
        "dispatch_status": "PENDING",
        "response_status": "AWAITING",
        "question_payload": {
            "question_id": f"q-{request_id}",
            "title": f"Active Ingestion Approval: {project} ({directive_id})",
            "context": context,
            "table_deltas": deltas,
            "options": options,
            "package_path": str(package_path) if package_path else None,
        },
        "authentic_response": None,
        "extensions": {
            "package_path": str(package_path) if package_path else None,
            "intake_package_path": str(intake_package_path) if intake_package_path else None,
        },
    }
    if intake_package_path:
        payload["intake_package_path"] = str(intake_package_path)
    return _seal_contract(payload)


def _resolve_clearance_ledger_paths(ledger_path: Optional[Path]) -> Tuple[Path, Path]:
    if not ledger_path:
        from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths
        return resolve_ledger_paths()
    l_path = Path(ledger_path).resolve()
    if l_path.suffix == ".sqlite":
        return l_path, l_path.with_suffix(".json")
    return l_path.with_suffix(".sqlite"), l_path


def _matches_clearance_scope(
    record: Dict[str, Any], directive_id: str, run_id: str, delta_digest: str, target_keys: set[str]
) -> bool:
    rec_id = str(record.get("decision_id") or "")
    meta = record.get("metadata") or {}
    meta_id = str(meta.get("decision_id") or "")
    meta_directive = str(meta.get("directive_id") or "")
    meta_run = str(meta.get("run_id") or "")
    meta_delta = str(meta.get("delta_digest") or meta.get("proposed_delta_digest") or "")

    matches = (
        (directive_id and meta_directive == directive_id)
        or (run_id and meta_run == run_id)
        or (rec_id in target_keys)
        or (meta_id in target_keys)
        or (bool(meta_delta) and meta_delta in target_keys)
    )
    if not matches:
        return False
    if delta_digest and meta_delta and meta_delta != delta_digest:
        return False
    return True


def _validate_clearance_record_operator(record: Dict[str, Any], meta: Dict[str, Any]) -> Optional[Tuple[bool, str]]:
    auth_type = str(record.get("authority_type") or meta.get("authority_type") or "").upper()
    if auth_type == "AUTONOMOUS":
        return False, "AUTONOMOUS_AUTHORITY_REJECTED: Active ingestion mutations strictly require authentic human approval."
    op_id = str(record.get("operator_id") or meta.get("operator_id") or "")
    if op_id.startswith("engine_") or meta.get("signer_role") == "AUTONOMOUS_ENGINE" or "engine" in op_id.lower():
        return False, "ENGINE_SIGNATURE_REJECTED: Engine signatures cannot authorize active ingestion mutations."
    if (record.get("operator_verified") or meta.get("operator_verified")) and not op_id:
        return False, "UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM: Boolean verified marker without authentic human origin is rejected."
    origin = meta.get("approval_origin") or meta.get("origin_verification") or op_id
    is_valid, origin_msg = validate_approval_origin(origin)
    if not is_valid:
        return False, f"SYNTHETIC_APPROVAL_REJECTED: {origin_msg}"
    return None


def _evaluate_clearance_record(record: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    meta = record.get("metadata") or {}
    op_check = _validate_clearance_record_operator(record, meta)
    if op_check is not None:
        return op_check

    outcome = str(record.get("outcome_type") or "").upper()
    selected = record.get("selected_option") or record.get("selected_key")
    selected_str = str(selected or "").strip()
    selected_lower = selected_str.lower()
    if (
        selected_str.upper() in ("DECLINED", "CANCELLED", "OPT_DECLINE", "HOLD", "REJECT", "REJECTED")
        or any(tok in selected_lower for tok in ("decline", "reject", "cancel"))
        or outcome == "OVERRIDDEN"
    ):
        return False, f"DECLINED: {selected_str or 'Ingestion was explicitly declined by operator.'}"

    if outcome in ("AFFIRMED", "RATIFIED", "RESOLVED") or selected:
        return True, f"RATIFIED: Operator '{record.get('operator_id')}' approved ingestion."

    return None, ""


def _check_sqlite_ledger_chain(s_path: Path) -> Optional[Tuple[bool, str]]:
    if not s_path.is_file():
        return None
    from storage.connection import create_sqlite_conn
    conn = create_sqlite_conn(s_path, readonly=True)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hitl_decisions'")
    has_table = cur.fetchone() is not None
    conn.close()
    if not has_table:
        return None
    from dual_pipeline.hitl_governance_core.decision_record_writer import verify_chain_integrity
    chain_ok, chain_msg = verify_chain_integrity(s_path)
    if chain_ok:
        return None
    from dual_pipeline.hitl_governance_core.ledger import _validate_record_chain
    from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore
    try:
        sq_store = HITLSQLiteStore(db_path=s_path)
        _validate_record_chain(sq_store.get_history())
        return None
    except Exception as exc:
        return False, f"LEDGER_CHAIN_INTEGRITY_FAILURE: {chain_msg} | {exc}"


def check_active_ingestion_clearance(
    *,
    run_id: str,
    directive_id: str,
    delta_digest: str,
    ledger_path: Optional[Path] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Check if authentic operator approval is already recorded for this exact scope."""
    s_path, j_path = _resolve_clearance_ledger_paths(ledger_path)
    chain_err = _check_sqlite_ledger_chain(s_path)
    if chain_err is not None:
        return chain_err[0], chain_err[1], None

    store = HITLDecisionStore(ledger_path=j_path, sqlite_path=s_path if s_path.is_file() else None)
    history = store.get_history()

    target_keys = {k for k in (directive_id, run_id, delta_digest) if k}

    for record in reversed(history):
        if not _matches_clearance_scope(record, directive_id, run_id, delta_digest, target_keys):
            continue
        is_cleared, reason = _evaluate_clearance_record(record)
        if is_cleared is not None:
            return is_cleared, reason, record

    return False, "UNRATIFIED: No valid prior operator resolution found for ingestion scope.", None



def dispatch_and_persist_approval_request(
    request: Dict[str, Any],
    transport: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ledger_path: Optional[Path] = None,
    storage_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Persist request, dispatch interactive question, and process response if immediate."""
    root = Path(storage_root or request.get("runtime_root") or "/tmp").resolve()
    req_dir = root / "approval_requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / f"{request['request_id']}.json"

    if req_file.is_file():
        try:
            existing = read_json(req_file)
            expected_digest = _seal_contract(existing).get("canonical_digest", {}).get("value")
            actual_digest = (existing.get("canonical_digest") or {}).get("value")
            if (
                existing.get("response_status") in ("APPROVED", "DECLINED")
                and actual_digest
                and actual_digest == expected_digest
                and existing.get("proposed_delta_digest") == request.get("proposed_delta_digest")
                and existing.get("source_package_digest") == request.get("source_package_digest")
            ):
                return existing
        except Exception:
            pass

    is_cleared, cl_msg, prior_rec = check_active_ingestion_clearance(
        run_id=request.get("run_id", ""),
        directive_id=request.get("directive_id", ""),
        delta_digest=request.get("proposed_delta_digest", ""),
        ledger_path=ledger_path,
    )
    if prior_rec is not None:
        updated = dict(request)
        outcome = "APPROVED" if is_cleared else "DECLINED"
        updated["dispatch_status"] = "PRE_APPROVED" if is_cleared else "SKIPPED"
        updated["response_status"] = outcome
        updated["authentic_response"] = {
            "selected_option": prior_rec.get("selected_option") or prior_rec.get("selected_key"),
            "recorded_at": prior_rec.get("recorded_at"),
            "entry_digest": prior_rec.get("entry_digest"),
            "is_approved": is_cleared,
            "reused_prior_clearance": True,
        }
        sealed = _seal_contract(updated)
        write_json(req_file, sealed)
        return sealed

    updated = dict(request)
    q_payload = updated["question_payload"]
    dispatch_res = dispatch_interactive_question(
        question=f"{q_payload['title']}\n\n{q_payload['context']}",
        options=[opt["label"] for opt in q_payload["options"]],
        transport=transport,
        request_id=updated["request_id"],
    )

    updated["dispatch_status"] = "DISPATCHED"
    updated["extensions"]["display_receipt"] = dispatch_res.get("display_receipt")
    _handle_immediate_dispatch_result(updated, q_payload, dispatch_res, ledger_path)
    sealed = _seal_contract(updated)
    write_json(req_file, sealed)
    return sealed


def _record_immediate_approval(
    updated: Dict[str, Any],
    q_payload: Dict[str, Any],
    raw_res: Any,
    ledger_path: Optional[Path],
) -> None:
    sel = raw_res.get("selected_option") if isinstance(raw_res, dict) else str(raw_res)
    is_decline = "decline" in str(sel).lower() or "reject" in str(sel).lower()
    op_id = raw_res.get("operator_id", "operator") if isinstance(raw_res, dict) else "operator"
    origin = raw_res.get("origin", "operator_ui") if isinstance(raw_res, dict) else "operator_ui"
    s_path, j_path = _resolve_clearance_ledger_paths(ledger_path)
    from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer

    rec = record_operator_answer(
        decision_id=updated["request_id"],
        category="INGESTION_MUTATION",
        question=q_payload["title"],
        options=q_payload["options"],
        selected_option=sel,
        rationale="Operator verified candidate publication",
        operator_origin=origin,
        sqlite_path=s_path,
        json_path=j_path,
        metadata={
            "run_id": updated["run_id"],
            "directive_id": updated["directive_id"],
            "delta_digest": updated["proposed_delta_digest"],
            "outcome_type": "OVERRIDDEN" if is_decline else "AFFIRMED",
            "operator_id": op_id,
        },
    )
    updated["response_status"] = "DECLINED" if is_decline else "APPROVED"
    updated["authentic_response"] = {
        "selected_option": sel,
        "recorded_at": rec.recorded_at,
        "entry_digest": rec.entry_digest,
        "is_approved": not is_decline,
    }


def _extract_question_allowed_options(q_payload: Dict[str, Any]) -> List[str]:
    opts: list[str] = []
    for opt in q_payload.get("options", []):
        if isinstance(opt, dict):
            if opt.get("label"):
                opts.append(str(opt["label"]))
            if opt.get("option_id"):
                opts.append(str(opt["option_id"]))
        elif isinstance(opt, str):
            opts.append(opt)
    return opts


def _handle_immediate_dispatch_result(
    updated: Dict[str, Any],
    q_payload: Dict[str, Any],
    dispatch_res: Dict[str, Any],
    ledger_path: Optional[Path],
) -> None:
    if dispatch_res.get("status") != "COMPLETED" or "response" not in dispatch_res:
        return
    raw_res = dispatch_res["response"].get("result")
    if isinstance(raw_res, dict) and "result" in raw_res and isinstance(raw_res["result"], (dict, str)):
        raw_res = raw_res["result"]
    if isinstance(raw_res, dict) and raw_res.get("status") in ("DISPATCHED", "PENDING", "AWAITING"):
        return
    opts = _extract_question_allowed_options(q_payload)
    is_valid, val_msg = validate_approval_response_signature(raw_res, opts)
    if not is_valid:
        if val_msg.startswith("NO_SELECTION") or val_msg.startswith("EMPTY_RESPONSE"):
            return
        updated["response_status"] = "DECLINED"
        updated["authentic_response"] = {"status": "INVALID", "reason": val_msg}
    else:
        _record_immediate_approval(updated, q_payload, raw_res, ledger_path)


def _check_prior_request_resolution(
    request: Dict[str, Any], req_file: Path, store: HITLDecisionStore
) -> Optional[Tuple[bool, str, Dict[str, Any]]]:
    if request.get("response_status") in ("APPROVED", "DECLINED") and request.get("authentic_response"):
        is_app = (request["response_status"] == "APPROVED")
        msg = "AUTHORIZED: Prior operator approval restored without re-persisting." if is_app else "DECLINED: Prior operator decline restored without re-persisting."
        return is_app, msg, request

    existing_rec = next((r for r in reversed(store.get_history()) if r.get("decision_id") == request["request_id"]), None)
    if existing_rec is not None:
        is_dec = (
            str(existing_rec.get("outcome_type") or "").upper() == "OVERRIDDEN"
            or "decline" in str(existing_rec.get("selected_key") or "").lower()
            or "reject" in str(existing_rec.get("selected_key") or "").lower()
        )
        request["response_status"] = "DECLINED" if is_dec else "APPROVED"
        request["authentic_response"] = {
            "selected_option": existing_rec.get("selected_option") or existing_rec.get("selected_key"),
            "recorded_at": existing_rec.get("recorded_at"),
            "entry_digest": existing_rec.get("entry_digest"),
            "is_approved": not is_dec,
            "reused_prior_clearance": True,
        }
        sealed = _seal_contract(request)
        write_json(req_file, sealed)
        outcome_msg = "DECLINED: Prior operator decline restored from ledger." if is_dec else "AUTHORIZED: Prior operator approval restored from ledger."
        return not is_dec, outcome_msg, sealed
    return None


def _extract_operator_id(operator_response: Dict[str, Any]) -> str:
    sig = str(operator_response.get("signature") or "")
    op_id = operator_response.get("operator_id")
    if not op_id and sig.startswith("sig:"):
        parts = sig.split(":")
        if len(parts) >= 3:
            op_id = parts[-1]
    return op_id or "human_operator"


def _persist_resumed_approval(
    request: Dict[str, Any],
    req_file: Path,
    store: HITLDecisionStore,
    operator_response: Dict[str, Any],
    selected: Any,
) -> Tuple[bool, str, Dict[str, Any]]:
    is_decline = "decline" in str(selected).lower() or "reject" in str(selected).lower()
    op_id = _extract_operator_id(operator_response)
    origin = operator_response.get("origin") or op_id
    from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer
    s_path, j_path = _resolve_clearance_ledger_paths(store.ledger_path)

    rec = record_operator_answer(
        decision_id=request["request_id"],
        category="INGESTION_MUTATION",
        question=request["question_payload"]["title"],
        options=request["question_payload"]["options"],
        selected_option=selected,
        rationale=operator_response.get("rationale", "Operator verified approval continuation"),
        operator_origin=origin,
        sqlite_path=s_path,
        json_path=j_path,
        metadata={
            "run_id": request["run_id"],
            "directive_id": request["directive_id"],
            "delta_digest": request["proposed_delta_digest"],
            "outcome_type": "OVERRIDDEN" if is_decline else "AFFIRMED",
            "operator_id": op_id,
        },
    )

    request["response_status"] = "DECLINED" if is_decline else "APPROVED"
    request["authentic_response"] = {
        "selected_option": selected,
        "recorded_at": rec.recorded_at,
        "entry_digest": rec.entry_digest,
        "is_approved": not is_decline,
    }

    sealed = _seal_contract(request)
    write_json(req_file, sealed)
    outcome_msg = "DECLINED: Operator declined publication." if is_decline else "AUTHORIZED: Operator approved publication."
    return not is_decline, outcome_msg, sealed


def resume_ingestion_with_approval(
    request_id: str,
    operator_response: Dict[str, Any],
    storage_root: Path,
    ledger_path: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Resume a pending ingestion workflow with an authentic operator response."""
    req_file = storage_root / "approval_requests" / f"{request_id}.json"
    if not req_file.is_file():
        raise FileNotFoundError(f"Approval request not found: {req_file}")

    request = read_json(req_file)
    delta_digest = request.get("proposed_delta_digest")
    pkg_digest = request.get("source_package_digest")
    if not delta_digest or delta_digest == ("0" * 64) or not pkg_digest or pkg_digest == ("0" * 64):
        return False, "INVALID_DIGEST: Approval request contains placeholder zero digest.", request

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    if request.get("expires_at") and now_iso > request["expires_at"]:
        request["response_status"] = "EXPIRED"
        sealed_expired = _seal_contract(request)
        write_json(req_file, sealed_expired)
        return False, "EXPIRED: Approval request has expired.", sealed_expired

    store = HITLDecisionStore(ledger_path=ledger_path)
    prior_res = _check_prior_request_resolution(request, req_file, store)
    if prior_res is not None:
        return prior_res

    q_options = _extract_question_allowed_options(request["question_payload"])
    is_valid, reason = validate_approval_response_signature(operator_response, q_options)
    if not is_valid:
        return False, reason, request

    selected = operator_response.get("selected_option") or operator_response.get("selected_key")
    return _persist_resumed_approval(request, req_file, store, operator_response, selected)
