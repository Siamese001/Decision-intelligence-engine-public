#!/usr/bin/env python3
"""Runtime Tool Adapter for Interactive Operator Questioning.

Under Decision D7 (resolved autonomously at 35% margin), this is the SINGLE allowlisted
file in the entire codebase permitted to reference the legacy IDE runtime tool name.

Responsibilities:
1. Translate outgoing calls from repository SSOT `ask_user_question` to host IDE name `ask_question`.
2. Normalize incoming results from the host IDE runtime back to canonical format.
3. Emit an immutable audit record documenting runtime tool adaptation per Decision D7.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from config.hitl_tool_name import ASK_USER_QUESTION

logger = logging.getLogger(__name__)

# ==============================================================================
# RUNTIME TOOL IDENTIFIERS (THE SINGLE ALLOWLISTED RESIDENCE OF LEGACY IDE NAME)
# ==============================================================================

CANONICAL_TOOL_NAME: str = ASK_USER_QUESTION
IDE_RUNTIME_TOOL_NAME: str = "ask_question"  # Permitted solely in this adapter under Decision D7


def get_runtime_target_tool_name(mode: str = "ide") -> str:
    """Resolve the host execution environment's tool name.

    Args:
        mode: Execution context ('ide' maps to host IDE name; 'native' preserves canonical name).
    """
    if mode == "ide":
        return IDE_RUNTIME_TOOL_NAME
    return CANONICAL_TOOL_NAME


def is_operator_question_payload(arguments: Dict[str, Any]) -> bool:
    """Return True if arguments conform to interactive operator questioning.

    Protects unrelated tools (such as DeepWiki ask_question) from being intercepted
    or altered by operator questioning adapters. DeepWiki arguments use repoName/repo_name
    without a 'questions' list.
    """
    if not isinstance(arguments, dict):
        return False
    if "repoName" in arguments or "repo_name" in arguments:
        return False
    return "questions" in arguments


def adapt_outgoing_tool_call(
    tool_name: str, arguments: Dict[str, Any], target_mode: str = "ide"
) -> Tuple[str, Dict[str, Any]]:
    """Adapt a canonical tool call to the runtime environment.

    Translates `ask_user_question` to `ask_question` when running under IDE mode.
    Preserves unrelated tools such as DeepWiki ask_question without modification.
    """
    if tool_name == CANONICAL_TOOL_NAME:
        target_name = get_runtime_target_tool_name(mode=target_mode)
        return target_name, dict(arguments)

    if tool_name == IDE_RUNTIME_TOOL_NAME:
        if not is_operator_question_payload(arguments):
            # Unrelated tool call (e.g. DeepWiki): preserve exactly as-is
            return tool_name, arguments
        target_name = get_runtime_target_tool_name(mode=target_mode)
        return target_name, dict(arguments)

    return tool_name, arguments


def adapt_incoming_tool_result(
    runtime_tool_name: str, result: Any
) -> Dict[str, Any]:
    """Normalize tool execution results from the runtime environment.

    Returns a standardized dictionary containing the result and provenance metadata.
    """
    is_adapted = (runtime_tool_name != CANONICAL_TOOL_NAME)
    audit = "Mapped canonical ask_user_question to runtime ask_question per Decision D7." if is_adapted else None
    return {"canonical_tool_name": CANONICAL_TOOL_NAME, "runtime_tool_name": runtime_tool_name, "adapted": is_adapted, "result": result, "decision_d7_audit": audit}


def format_runtime_question_call(
    question: str,
    options: Sequence[Union[str, Dict[str, Any]]],
    is_multi_select: bool = False,
    target_mode: str = "ide",
) -> Dict[str, Any]:
    """Construct a fully-formed runtime tool call packet for operator questioning.

    Args:
        question: The plain-language, non-jargon question string.
        options: Formatted option strings or structured option dictionaries.
        is_multi_select: Whether multiple options may be selected simultaneously.
        target_mode: 'ide' to target the host IDE tool declaration, or 'native' for canonical.

    Returns:
        Dictionary conforming to the runtime tool call invocation contract.
    """
    formatted_options: List[str] = []
    for opt in options:
        if isinstance(opt, dict):
            # Prefer display_text or label
            txt = opt.get("display_text") or opt.get("label") or str(opt)
            formatted_options.append(str(txt))
        else:
            formatted_options.append(str(opt))

    target_tool = get_runtime_target_tool_name(mode=target_mode)
    return {
        "tool": target_tool,
        "arguments": {
            "questions": [
                {
                    "question": question,
                    "options": formatted_options,
                    "is_multi_select": is_multi_select,
                }
            ]
        },
    }


def get_adapter_audit_record() -> Dict[str, Any]:
    """Generate an immutable audit record of the active adapter binding."""
    return {
        "canonical_name": CANONICAL_TOOL_NAME,
        "runtime_target_name": IDE_RUNTIME_TOOL_NAME,
        "governing_decision": "D7",
        "margin_delta": 0.35,
        "resolution_type": "AUTONOMOUS",
        "adapter_file": "dual_pipeline/hitl_governance_core/runtime_tool_adapter.py",
        "status": "ACTIVE",
    }


def dispatch_mutation_approval(
    proposal: Any,
    runtime_root: Union[str, Any],
    host_tool_invoker: Optional[Any] = None,
    target_mode: str = "ide",
) -> Any:
    """Convenience bridge to dispatch a mutation proposal through HostApprovalDispatcher."""
    from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
        HostApprovalDispatcher,
    )

    dispatcher = HostApprovalDispatcher(runtime_root=runtime_root)
    return dispatcher.dispatch(
        proposal=proposal,
        host_tool_invoker=host_tool_invoker,
        target_mode=target_mode,
    )


def discover_runtime_capabilities(
    target_mode: str = "ide",
    host_qualification_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Discover host runtime capabilities for interactive questioning.

    Fails closed (supported: False) unless explicit host qualification evidence
    is provided with valid trace/acknowledgment.
    """
    runtime_tool = get_runtime_target_tool_name(mode=target_mode)
    has_evidence = bool(
        host_qualification_evidence
        and isinstance(host_qualification_evidence, dict)
        and (
            host_qualification_evidence.get("trace_id")
            or host_qualification_evidence.get("acknowledged")
            or host_qualification_evidence.get("status") == "QUALIFIED"
        )
    )
    return {
        "supported": has_evidence,
        "qualified": has_evidence,
        "canonical_tool": CANONICAL_TOOL_NAME,
        "runtime_tool": runtime_tool,
        "target_mode": target_mode,
        "host_qualification_evidence": host_qualification_evidence,
        "capabilities": (
            [
                "atomic_question_presentation",
                "interactive_clickable_options",
                "durable_delivery_acknowledgment",
                "layman_translation",
            ]
            if has_evidence
            else []
        ),
        "status": "QUALIFIED" if has_evidence else "UNQUALIFIED",
    }


def dispatch_interactive_question(
    question: str,
    options: Sequence[Union[str, Dict[str, Any]]],
    is_multi_select: bool = False,
    transport: Any | None = None,
    target_mode: str = "ide",
    request_id: str | None = None,
    timeout_seconds: float | None = None,
) -> Dict[str, Any]:
    """Dispatch an interactive question via host runtime or transport double.

    Emits distinct display receipt and response receipt bindings.
    Guarantees delivery_status is PENDING until transport acknowledgment.
    """
    import datetime as dt
    import hashlib

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    call_packet = format_runtime_question_call(
        question=question,
        options=options,
        is_multi_select=is_multi_select,
        target_mode=target_mode,
    )

    q_digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    req_id = request_id or f"req-{q_digest[:16]}"
    display_receipt = {
        "display_receipt_id": f"disp-{req_id}",
        "request_id": req_id,
        "dispatched_at": now_iso,
        "question_digest": q_digest,
        "delivery_status": "PENDING",
        "runtime_tool": call_packet["tool"],
    }

    if transport is not None:
        try:
            from config.request_deadline import suspend_deadline
            eff_timeout = timeout_seconds or 60.0
            with suspend_deadline(max_suspend_seconds=eff_timeout):
                raw_res = transport(call_packet)
            display_receipt["delivery_status"] = "DELIVERED"
            display_receipt["acknowledged_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            adapted_res = adapt_incoming_tool_result(call_packet["tool"], raw_res)
            return {
                "status": "COMPLETED",
                "request_id": req_id,
                "display_receipt": display_receipt,
                "response": adapted_res,
                "transport_double": getattr(transport, "__name__", "custom_transport"),
            }
        except Exception as exc:
            display_receipt["delivery_status"] = "FAILED"
            return {
                "status": "DISPATCH_FAILED",
                "request_id": req_id,
                "display_receipt": display_receipt,
                "error": str(exc),
            }

    import sys
    opts_txt = "\n".join(f"  [{i}] {o if isinstance(o, str) else o.get('label', str(o))}" for i, o in enumerate(options, 1))
    print(f"\n{'='*80}\n[ACTION REQUIRED: ask_user_question]\nRequest ID: {req_id}\n\nQuestion:\n{question}\n\nOptions:\n{opts_txt}\n{'='*80}\n", file=sys.stderr, flush=True)
    display_receipt["presentation_status"] = "EMITTED_FOR_PRESENTATION"
    return {
        "status": "AWAITING_RESPONSE",
        "request_id": req_id,
        "display_receipt": display_receipt,
        "call_packet": call_packet,
    }


def resolve_or_dispatch_decision(
    decision_request: Any,
    runtime_root: Optional[Union[str, Any]] = None,
    transport: Optional[Callable[[Dict[str, Any]], Any]] = None,
    target_mode: str = "ide",
    host_qualification_evidence: Optional[Dict[str, Any]] = None,
) -> Any:
    """Route decision through governed autonomous resolution before UI dispatch.

    If eligible: resolves autonomously and returns result with 0 transport calls.
    If human review required: invokes transport if provided and qualified.
    """
    from dual_pipeline.hitl_governance_core.autonomous_resolution.service import (
        resolve_governed_decision,
    )

    return resolve_governed_decision(
        request=decision_request,
        runtime_root=runtime_root,
        transport=transport,
        target_mode=target_mode,
        host_qualification_evidence=host_qualification_evidence,
    )


def _extract_op_id_and_origin(response: Dict[str, Any]) -> Tuple[str, str]:
    op_id = str(response.get("operator_id") or "")
    sig = str(response.get("signature") or "")
    if not op_id and sig.startswith("sig:"):
        parts = sig.split(":")
        if len(parts) >= 3:
            op_id = parts[-1]
    meta = response.get("metadata") or {}
    origin = meta.get("approval_origin") or meta.get("origin_verification") or response.get("origin") or op_id
    return op_id, origin


def _extract_selected_option(response: Dict[str, Any]) -> Optional[str]:
    selected = response.get("selected_option") or response.get("selected_key")
    if not selected and isinstance(response.get("result"), str):
        selected = response["result"]
    return selected


def _extract_response_components(response: Any) -> Tuple[Optional[str], str, str]:
    if not isinstance(response, dict):
        sel = response if isinstance(response, str) else None
        return sel, "operator", "operator"
    inner = response.get("result")
    if isinstance(inner, dict):
        response = inner
    selected = _extract_selected_option(response)
    op_id, origin = _extract_op_id_and_origin(response)
    return selected, op_id, origin


def _validate_operator_identity(op_id: str) -> Tuple[bool, str]:
    if not op_id or not op_id.strip():
        return False, "UNAUTHENTICATED_OPERATOR: Operator response lacks an authentic operator identity."
    from dual_pipeline.hitl_governance_core.scoped_autonomy import is_autonomous_alias
    if is_autonomous_alias(op_id) or "engine" in op_id.lower() or op_id in ("mock_operator", "fake_operator"):
        return False, f"UNAUTHENTICATED_OPERATOR: Autonomous alias '{op_id}' cannot grant human approval."
    return True, ""


def _matches_allowed_options(norm_selected: str, allowed_options: Sequence[str]) -> bool:
    import re

    clean_sel = re.sub(r"^\(recommended\)\s*", "", norm_selected).strip()
    label_sel = clean_sel.split(" (confidence:")[0].split(" - ")[0].split(". ")[0].strip()

    for opt in allowed_options:
        no = str(opt).strip().lower()
        clean_opt = re.sub(r"^\(recommended\)\s*", "", no).strip()
        label_opt = clean_opt.split(" (confidence:")[0].split(" - ")[0].split(". ")[0].strip()

        if norm_selected in (no, clean_opt, label_opt):
            return True
        if clean_sel in (no, clean_opt, label_opt) or label_sel in (no, clean_opt, label_opt):
            return True
        if no.startswith(f"{clean_sel} (") or clean_opt.startswith(f"{clean_sel} ("):
            return True
    return False


def validate_approval_response_signature(
    response: Any,
    allowed_options: Sequence[str],
) -> Tuple[bool, str]:
    """Validate that an operator response is non-empty, authentic, and matches declared options."""
    from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin

    if response is None:
        return False, "EMPTY_RESPONSE: Operator response is None."
    if not isinstance(response, (dict, str)):
        return False, f"MALFORMED_RESPONSE: Unsupported response type {type(response).__name__}."

    selected, operator_id, origin = _extract_response_components(response)
    if not selected or not str(selected).strip():
        return False, "NO_SELECTION: Response contains no selected option."

    is_valid_origin, origin_reason = validate_approval_origin(origin)
    if not is_valid_origin:
        return False, f"SYNTHETIC_APPROVAL_REJECTED: {origin_reason}"

    if isinstance(response, dict):
        ok_id, id_msg = _validate_operator_identity(operator_id)
        if not ok_id:
            return False, id_msg

    norm_selected = str(selected).strip().lower()
    if not _matches_allowed_options(norm_selected, allowed_options):
        return False, f"OPTION_MISMATCH: Selected '{selected}' not found in allowed options."

    return True, f"VALID_AUTHENTIC_RESPONSE: Operator '{origin}' selected '{selected}'."
