#!/usr/bin/env python3
"""dual_pipeline/hitl_governance_core/host_approval_dispatcher.py

Host UI Approval Dispatcher for Authoritative Mutation Proposals (Wave 1).
Lifecycle: PREPARED -> AWAITING_APPROVAL -> APPROVED -> APPLYING -> ACTIVATED
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from config.hitl_tool_name import ASK_USER_QUESTION
from dual_pipeline.hitl_governance_core.question_builder import (
    build_mutation_approval_question,
)
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    adapt_incoming_tool_result,
    adapt_outgoing_tool_call,
)
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin

logger = logging.getLogger(__name__)


class ProposalStatus(str, Enum):
    PREPARED = "PREPARED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLYING = "APPLYING"
    ACTIVATED = "ACTIVATED"


class DeliveryState(str, Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True)
class MutationProposal:
    proposal_id: str
    meeting_name: str
    source_hash: str
    output_hashes: Dict[str, str]
    intake_hash: str
    target_graph: str
    runtime_root: str
    expected_base_generation: Union[int, str]
    deltas: Dict[str, Any]
    validation_evidence: Dict[str, Any]
    rollback_scope: str

    def compute_proposal_digest(self) -> str:
        payload = {
            "proposal_id": self.proposal_id,
            "meeting_name": self.meeting_name,
            "source_hash": self.source_hash,
            "output_hashes": dict(sorted(self.output_hashes.items())),
            "intake_hash": self.intake_hash,
            "target_graph": self.target_graph,
            "runtime_root": self.runtime_root,
            "expected_base_generation": str(self.expected_base_generation),
            "deltas": self.deltas,
            "validation_evidence": self.validation_evidence,
            "rollback_scope": self.rollback_scope,
        }
        b = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(b).hexdigest()

    def format_detail_view(self) -> str:
        lines = [
            f"# Mutation Proposal Details: {self.meeting_name}",
            f"- **Proposal ID**: `{self.proposal_id}`",
            f"- **Proposal Digest**: `{self.compute_proposal_digest()}`",
            f"- **Source Hash**: `{self.source_hash}`",
            f"- **Intake Hash**: `{self.intake_hash}`",
            f"- **Target Graph**: `{self.target_graph}`",
            f"- **Expected Base Generation**: `{self.expected_base_generation}`",
            f"- **Rollback Reference**: `{self.rollback_scope}`",
            "### Output Artifact Hashes:",
        ]
        lines.extend([f"  - Output {oid}: `{ohash}`" for oid, ohash in sorted(self.output_hashes.items())])
        lines.append("### Planned Graph Deltas:")
        lines.extend([f"  - {k}: {v}" for k, v in sorted(self.deltas.items())])
        lines.append("### Validation Evidence:")
        lines.extend([f"  - {k}: {v}" for k, v in sorted(self.validation_evidence.items())])
        return "\n".join(lines)


@dataclass(frozen=True)
class DispatchResult:
    request_id: str
    proposal_digest: str
    delivery_state: DeliveryState
    status: ProposalStatus
    authorized: bool
    action: str
    detail_view: Optional[str] = None
    response_event_id: Optional[str] = None
    operator_origin: Optional[str] = None
    approval_receipt: Optional[Dict[str, Any]] = None
    diagnostics: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["delivery_state"] = self.delivery_state.value
        res["status"] = self.status.value
        return res


def _storage_file(runtime_root: Union[Path, str]) -> Path:
    p = Path(runtime_root) / "approval" / "host_approval_requests.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def persist_approval_request(record: Dict[str, Any], runtime_root: Union[Path, str]) -> None:
    fp = _storage_file(runtime_root)
    existing: Dict[str, Any] = {}
    if fp.is_file():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing[record["request_id"]] = record
    fp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")


def load_approval_request(request_id: str, runtime_root: Union[Path, str]) -> Optional[Dict[str, Any]]:
    fp = _storage_file(runtime_root)
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8")).get(request_id)
    except Exception:
        return None


def find_approval_by_digest(digest_val: str, runtime_root: Union[Path, str]) -> Optional[Dict[str, Any]]:
    fp = _storage_file(runtime_root)
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        matches = [r for r in data.values() if r.get("proposal_digest") == digest_val]
        if not matches:
            return None
        return sorted(
            matches,
            key=lambda x: (x.get("status") == ProposalStatus.APPROVED.value, x.get("updated_at", "")),
            reverse=True,
        )[0]
    except Exception:
        return None


def _classify_mutation_selection(sel_text: str) -> str:
    import re

    lower = sel_text.strip().lower()
    clean_sel = re.sub(r"^\(recommended\)\s*", "", lower).strip()
    label_sel = clean_sel.split(" (confidence:")[0].split(" - ")[0].split(". ")[0].strip()

    if any(clean_sel.startswith(p) or label_sel.startswith(p) for p in ("do not", "not ", "dont", "never")):
        return "MALFORMED"

    if label_sel in ("review details", "review", "details") or clean_sel.startswith("review details"):
        return "REVIEW_DETAILS"
    if label_sel in ("reject", "decline", "cancelled") or clean_sel.startswith("reject"):
        return "REJECT"
    if label_sel in ("approve mutation", "approve") or clean_sel.startswith("approve mutation") or clean_sel.startswith("approve ("):
        return "APPROVE"
    return "MALFORMED"


def _sync_mutation_approval_to_ledger(
    proposal: MutationProposal, request_id: str, dig: str, origin: str
) -> None:
    try:
        from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore

        store = HITLDecisionStore()
        history = store.get_history()
        if any(r.get("decision_id") == request_id for r in history):
            return
        q_payload = build_mutation_approval_question(proposal)
        derived_opts = [
            {
                "label": o.label,
                "confidence_score": o.confidence_score,
                "tier": "HIGH" if o.confidence_score >= 0.85 else ("MEDIUM" if o.confidence_score >= 0.50 else "LOW"),
            }
            for o in q_payload.options
        ]
        store.record_decision(
            decision_id=request_id,
            category="MUTATION_APPROVAL",
            prompt=f"Authorize knowledge graph publication for '{proposal.meeting_name}'",
            options=derived_opts,
            selected_option="Approve mutation",
            rationale="Authentic operator approval granted for graph mutation.",
            operator_id=origin,
            metadata={
                "proposal_id": proposal.proposal_id,
                "proposal_digest": dig,
                "meeting_name": proposal.meeting_name,
                "expected_base_generation": str(proposal.expected_base_generation),
                "approval_origin": origin,
                "outcome_type": "AFFIRMED",
            },
        )
    except Exception as err:
        logger.warning("Could not synchronize approval to HITLDecisionStore: %s", err)


class HostApprovalDispatcher:
    """Dispatches proposal review questions to host UI with anti-synthetic gating."""

    def __init__(
        self,
        runtime_root: Union[Path, str],
        host_scope: str = "antigravity-ide",
        thread_scope: Optional[str] = None,
    ) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.host_scope = host_scope
        self.thread_scope = thread_scope or f"thread-{uuid.uuid4().hex[:8]}"

    def _initial_record(self, proposal: MutationProposal, req_id: str, dig: str) -> Dict[str, Any]:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "request_id": req_id,
            "proposal_id": proposal.proposal_id,
            "proposal_digest": dig,
            "meeting_name": proposal.meeting_name,
            "source_hash": proposal.source_hash,
            "output_hashes": proposal.output_hashes,
            "intake_hash": proposal.intake_hash,
            "target_graph": proposal.target_graph,
            "runtime_root": proposal.runtime_root,
            "expected_base_generation": str(proposal.expected_base_generation),
            "deltas": proposal.deltas,
            "validation_evidence": proposal.validation_evidence,
            "rollback_scope": proposal.rollback_scope,
            "host_scope": self.host_scope,
            "thread_scope": self.thread_scope,
            "delivery_state": DeliveryState.PENDING.value,
            "status": ProposalStatus.AWAITING_APPROVAL.value,
            "created_at": now_iso,
            "updated_at": now_iso,
            "details_rendered_count": 0,
            "authorized": False,
        }

    def dispatch(
        self,
        proposal: MutationProposal,
        host_tool_invoker: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        target_mode: str = "ide",
    ) -> DispatchResult:
        dig = proposal.compute_proposal_digest()
        existing = find_approval_by_digest(dig, self.runtime_root)
        if existing and existing.get("expected_base_generation") == str(proposal.expected_base_generation):
            st = existing.get("status")
            deliv = DeliveryState(existing.get("delivery_state", DeliveryState.DELIVERED.value))
            if st == ProposalStatus.APPROVED.value and not existing.get("consumed", False):
                return DispatchResult(
                    request_id=existing["request_id"], proposal_digest=dig, delivery_state=deliv,
                    status=ProposalStatus.APPROVED, authorized=True, action="APPROVE",
                    approval_receipt=existing.get("approval_receipt"),
                    diagnostics="Existing unconsumed approval restored without re-prompting.",
                )
            if st == ProposalStatus.REJECTED.value:
                return DispatchResult(
                    request_id=existing["request_id"], proposal_digest=dig, delivery_state=deliv,
                    status=ProposalStatus.REJECTED, authorized=False, action="REJECT",
                    diagnostics="Proposal previously rejected; re-prompting prohibited.",
                )
            req_id = existing["request_id"]
            rec = existing
        else:
            req_id = f"req-{uuid.uuid4().hex[:12]}"
            rec = self._initial_record(proposal, req_id, dig)
            persist_approval_request(rec, self.runtime_root)

        d_sum = f"+{proposal.deltas.get('nodes_added', 0)} nodes, +{proposal.deltas.get('edges_added', 0)} edges"
        payload = build_mutation_approval_question(
            meeting_name=proposal.meeting_name,
            proposal_digest=dig[:16],
            expected_base_generation=proposal.expected_base_generation,
            delta_summary=d_sum,
        )

        canonical_tool = ASK_USER_QUESTION
        target_tool, adapted_args = adapt_outgoing_tool_call(
            canonical_tool, payload.to_tool_call(canonical_tool), target_mode=target_mode
        )

        if host_tool_invoker is None:
            rec["delivery_state"] = DeliveryState.FAILED.value
            rec["diagnostics"] = "Host interactive tool unavailable; delivery failed closed."
            persist_approval_request(rec, self.runtime_root)
            return DispatchResult(
                request_id=req_id, proposal_digest=dig, delivery_state=DeliveryState.FAILED,
                status=ProposalStatus.AWAITING_APPROVAL, authorized=False, action="DELIVERY_FAILED",
                diagnostics=rec["diagnostics"],
            )

        try:
            raw = host_tool_invoker(target_tool, adapted_args)
            norm = adapt_incoming_tool_result(target_tool, raw)
        except Exception as exc:
            rec["delivery_state"] = DeliveryState.DISCONNECTED.value
            rec["diagnostics"] = f"Host interaction disconnected: {exc}"
            persist_approval_request(rec, self.runtime_root)
            return DispatchResult(
                request_id=req_id, proposal_digest=dig, delivery_state=DeliveryState.DISCONNECTED,
                status=ProposalStatus.AWAITING_APPROVAL, authorized=False, action="DISCONNECTED",
                diagnostics=rec["diagnostics"],
            )

        return self.process_response(proposal=proposal, request_id=req_id, tool_result=norm["result"])

    def process_response(
        self, proposal: MutationProposal, request_id: str, tool_result: Any
    ) -> DispatchResult:
        dig = proposal.compute_proposal_digest()
        rec = load_approval_request(request_id, self.runtime_root) or self._initial_record(proposal, request_id, dig)
        from datetime import datetime, timezone
        rec["updated_at"] = datetime.now(timezone.utc).isoformat()

        sel_text = ""
        origin = "USER_INPUT"
        ev_id = f"ev-{uuid.uuid4().hex[:10]}"
        if isinstance(tool_result, dict):
            sel_text = str(tool_result.get("selected_option") or tool_result.get("answer") or "")
            origin = str(tool_result.get("origin") or "USER_INPUT")
            ev_id = str(tool_result.get("event_id") or ev_id)
        elif isinstance(tool_result, str):
            sel_text = tool_result

        is_auth, origin_msg = validate_approval_origin(origin)
        if not is_auth:
            rec["diagnostics"] = f"Rejected synthetic origin: {origin_msg}"
            persist_approval_request(rec, self.runtime_root)
            return DispatchResult(
                request_id=request_id, proposal_digest=dig, delivery_state=DeliveryState.DELIVERED,
                status=ProposalStatus.AWAITING_APPROVAL, authorized=False, action="REJECTED_SYNTHETIC_ORIGIN",
                response_event_id=ev_id, operator_origin=origin, diagnostics=origin_msg,
            )

        choice = _classify_mutation_selection(sel_text)
        if choice == "REVIEW_DETAILS":
            rec["details_rendered_count"] = rec.get("details_rendered_count", 0) + 1
            persist_approval_request(rec, self.runtime_root)
            return DispatchResult(
                request_id=request_id, proposal_digest=dig, delivery_state=DeliveryState.DELIVERED,
                status=ProposalStatus.AWAITING_APPROVAL, authorized=False, action="REVIEW_DETAILS",
                detail_view=proposal.format_detail_view(), response_event_id=ev_id,
                operator_origin=origin, diagnostics="Review details rendered. Zero mutation granted.",
            )

        if choice == "REJECT":
            rec["status"] = ProposalStatus.REJECTED.value
            persist_approval_request(rec, self.runtime_root)
            return DispatchResult(
                request_id=request_id, proposal_digest=dig, delivery_state=DeliveryState.DELIVERED,
                status=ProposalStatus.REJECTED, authorized=False, action="REJECT",
                response_event_id=ev_id, operator_origin=origin, diagnostics="Explicit rejection received.",
            )

        if choice == "APPROVE":
            receipt = {
                "request_id": request_id,
                "proposal_id": proposal.proposal_id,
                "proposal_digest": dig,
                "meeting_name": proposal.meeting_name,
                "source_hash": proposal.source_hash,
                "output_hashes": proposal.output_hashes,
                "intake_hash": proposal.intake_hash,
                "target_graph": proposal.target_graph,
                "runtime_root": proposal.runtime_root,
                "expected_base_generation": str(proposal.expected_base_generation),
                "deltas": proposal.deltas,
                "validation_evidence": proposal.validation_evidence,
                "rollback_scope": proposal.rollback_scope,
                "operator_origin": origin,
                "response_event_id": ev_id,
                "approved_at": rec["updated_at"],
                "status": ProposalStatus.APPROVED.value,
                "authorized": True,
            }
            rec["status"] = ProposalStatus.APPROVED.value
            rec["authorized"] = True
            rec["approval_receipt"] = receipt
            persist_approval_request(rec, self.runtime_root)
            _sync_mutation_approval_to_ledger(proposal, request_id, dig, origin)
            return DispatchResult(
                request_id=request_id, proposal_digest=dig, delivery_state=DeliveryState.DELIVERED,
                status=ProposalStatus.APPROVED, authorized=True, action="APPROVE",
                response_event_id=ev_id, operator_origin=origin, approval_receipt=receipt,
                diagnostics="Authentic operator approval received and bound to proposal.",
            )

        rec["diagnostics"] = f"Unrecognized choice: '{sel_text}'"
        persist_approval_request(rec, self.runtime_root)
        return DispatchResult(
            request_id=request_id, proposal_digest=dig, delivery_state=DeliveryState.DELIVERED,
            status=ProposalStatus.AWAITING_APPROVAL, authorized=False, action="MALFORMED_SELECTION",
            response_event_id=ev_id, operator_origin=origin, diagnostics=rec["diagnostics"],
        )


def check_cli_mutation_approval(
    source_path: Union[Path, str],
    metadata: Dict[str, Any],
    output_dir: Union[Path, str],
    run_kwargs: Dict[str, Any],
    host_tool_invoker: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
) -> bool:
    """Enforce Wave 1 host approval before permitting graph mutation flags."""
    should_mutate = bool(run_kwargs.get("mutate_graph") or run_kwargs.get("auto_mutate_on_pass"))
    if not should_mutate:
        return True

    src_p = Path(source_path)
    src_hash = hashlib.sha256(src_p.read_bytes()).hexdigest() if src_p.is_file() else ""
    out_dir_path = Path(output_dir)
    intake_p = out_dir_path / "intake_package.json"
    intake_hash = hashlib.sha256(intake_p.read_bytes()).hexdigest() if intake_p.is_file() else str(metadata.get("intake_hash") or "")
    out_hashes: Dict[str, str] = {}
    if out_dir_path.is_dir():
        for prefix, key in (("01_", "01_transcript"), ("02_", "02_recap"), ("03_", "03_graph")):
            matches = list(out_dir_path.glob(f"{prefix}*"))
            if matches and matches[0].is_file():
                out_hashes[key] = hashlib.sha256(matches[0].read_bytes()).hexdigest()

    proposal = MutationProposal(
        proposal_id=f"prop-{src_p.stem}",
        meeting_name=str(metadata.get("meeting_name") or src_p.stem),
        source_hash=src_hash,
        output_hashes=out_hashes,
        intake_hash=intake_hash,
        target_graph="CANONICAL_GRAPH.db",
        runtime_root=str(output_dir),
        expected_base_generation=str(metadata.get("expected_base_generation", "current")),
        deltas={"nodes_added": 0, "edges_added": 0, "source_retirements": []},
        validation_evidence={"cli_flag_requested": True},
        rollback_scope="HEAD",
    )
    dispatcher = HostApprovalDispatcher(runtime_root=output_dir)
    res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=host_tool_invoker)
    if not res.authorized:
        run_kwargs["mutate_graph"] = False
        run_kwargs["auto_mutate_on_pass"] = False
        logger.info("Host approval not authorized (%s); mutations disabled.", res.action)
        return False
    return True
