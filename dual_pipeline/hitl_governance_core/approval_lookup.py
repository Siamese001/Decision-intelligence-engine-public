"""
dual_pipeline/hitl_governance_core/approval_lookup.py

Governed HITL evidence and approval reference lookup.
Resolves explicitly bound ledger paths and immutable approval references,
distinguishing:
- RESOLVED_VALID: Cryptographically verified authentic operator approval matching subject/digest.
- REJECTED_OPERATOR_DECLINED: Found, but operator explicitly declined or rejected the proposal.
- REJECTED_SYNTHETIC: Found, but origin verification detected synthetic/automated approval.
- UNRESOLVED_REFERENCE: Entry ID / reference does not exist in the designated ledger checkpoint.
- DIGEST_MISMATCH: Entry found, but cryptographic entry digest or content hash does not match expected binding.
- SUBJECT_MISMATCH: Entry found, but subject SHA-256 does not match expected binding.
- INVALID_SIGNATURE: Entry found, but Ed25519 signature is cryptographically invalid.
- LEDGER_UNAVAILABLE: Ledger file is missing, empty, or unreadable at the specified path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dual_pipeline.hitl_governance_core.crypto import (
    _find_default_ledger_path,
    verify_hitl_approval_entry,
)
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin


class ApprovalResolutionStatus(str, Enum):
    RESOLVED_VALID = "RESOLVED_VALID"
    REJECTED_OPERATOR_DECLINED = "REJECTED_OPERATOR_DECLINED"
    REJECTED_SYNTHETIC = "REJECTED_SYNTHETIC"
    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    LEDGER_UNAVAILABLE = "LEDGER_UNAVAILABLE"


@dataclass(frozen=True)
class ApprovalResolutionResult:
    status: ApprovalResolutionStatus
    is_valid: bool
    is_rejected: bool
    is_unresolved: bool
    entry_id: str
    ledger_path: str
    reason: str
    entry: Optional[Dict[str, Any]] = None
    operator_id: Optional[str] = None
    subject_sha256: Optional[str] = None
    entry_digest: Optional[str] = None
    recorded_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "is_valid": self.is_valid,
            "is_rejected": self.is_rejected,
            "is_unresolved": self.is_unresolved,
            "entry_id": self.entry_id,
            "ledger_path": self.ledger_path,
            "reason": self.reason,
            "operator_id": self.operator_id,
            "subject_sha256": self.subject_sha256,
            "entry_digest": self.entry_digest,
            "recorded_at": self.recorded_at,
        }


def _resolve_target_ledger(
    explicit_path: Optional[Union[str, Path]],
    default_path: Optional[Union[str, Path]],
) -> Optional[Path]:
    """Resolves ledger file path adhering strictly to explicitly bound reference."""
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        return p
    if default_path:
        p = Path(default_path).expanduser().resolve()
        return p
    try:
        def_p = _find_default_ledger_path()
        return def_p.resolve() if def_p else None
    except Exception:
        return None


def _parse_ref_inputs(
    ref: Union[str, Dict[str, Any]],
    expected_subject_sha256: Optional[str],
    expected_digest: Optional[str],
) -> Tuple[str, Optional[Any], Optional[str], Optional[str]]:
    if isinstance(ref, dict):
        entry_id = str(ref.get("entry_id") or ref.get("decision_id") or "").strip()
        explicit_ledger = ref.get("ledger_path")
        exp_digest = expected_digest or ref.get("entry_digest") or ref.get("entry_sha256")
        exp_subject = expected_subject_sha256 or ref.get("subject_sha256")
    else:
        entry_id = str(ref).strip()
        explicit_ledger = None
        exp_digest = expected_digest
        exp_subject = expected_subject_sha256
    return entry_id, explicit_ledger, exp_digest, exp_subject


def _load_ledger_records(target_ledger: Path) -> Tuple[Optional[List[Any]], Optional[str]]:
    try:
        raw_text = target_ledger.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            return parsed.get("decisions", []), None
        if isinstance(parsed, list):
            return parsed, None
        return [], None
    except Exception as exc:
        return None, str(exc)


def _check_synthetic_origin(
    matched_entry: Dict[str, Any], payload: Dict[str, Any], entry_id: str, ledger_str: str, operator_id: Any
) -> Optional[ApprovalResolutionResult]:
    origin = payload.get("approval_origin") or payload.get("origin_verification") or operator_id
    is_origin_valid, origin_msg = validate_approval_origin(origin)
    if not is_origin_valid:
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.REJECTED_SYNTHETIC,
            is_valid=False,
            is_rejected=True,
            is_unresolved=False,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"REJECTED_SYNTHETIC: Synthetic or non-human approval origin: {origin_msg}",
            entry=matched_entry,
            operator_id=str(operator_id or ""),
        )
    return None


def _check_operator_decline(
    matched_entry: Dict[str, Any], payload: Dict[str, Any], entry_id: str, ledger_str: str, operator_id: Any
) -> Optional[ApprovalResolutionResult]:
    selected_option = str(matched_entry.get("selected_key") or matched_entry.get("selected_option") or payload.get("selected_key") or "").upper()
    outcome_type = str(matched_entry.get("outcome_type") or payload.get("outcome_type") or "").upper()
    if selected_option in ("DECLINED", "CANCELLED", "OPT_DECLINE", "REJECTED") or outcome_type in ("OVERRIDDEN", "REJECTED"):
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.REJECTED_OPERATOR_DECLINED,
            is_valid=False,
            is_rejected=True,
            is_unresolved=False,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"REJECTED_OPERATOR_DECLINED: Operator explicitly declined/overrode proposal ({selected_option}).",
            entry=matched_entry,
            operator_id=str(operator_id or ""),
        )
    return None


def _check_digest_and_subject(
    matched_entry: Dict[str, Any], payload: Dict[str, Any], entry_id: str, ledger_str: str,
    exp_digest: Optional[str], exp_subject: Optional[str], operator_id: Any,
) -> Optional[ApprovalResolutionResult]:
    actual_digest = matched_entry.get("entry_digest") or matched_entry.get("entry_sha256")
    if exp_digest and actual_digest and actual_digest != exp_digest:
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.DIGEST_MISMATCH,
            is_valid=False,
            is_rejected=False,
            is_unresolved=False,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"DIGEST_MISMATCH: Recorded digest '{actual_digest}' does not match expected '{exp_digest}'.",
            entry=matched_entry,
            entry_digest=actual_digest,
            operator_id=str(operator_id or ""),
        )

    actual_subject = matched_entry.get("subject_sha256") or payload.get("subject_sha256")
    if exp_subject and actual_subject and actual_subject != exp_subject:
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.SUBJECT_MISMATCH,
            is_valid=False,
            is_rejected=False,
            is_unresolved=False,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"SUBJECT_MISMATCH: Recorded subject '{actual_subject}' does not match expected '{exp_subject}'.",
            entry=matched_entry,
            subject_sha256=actual_subject,
            operator_id=str(operator_id or ""),
        )
    return None


def _validate_matched_entry(
    matched_entry: Dict[str, Any],
    entry_id: str,
    ledger_str: str,
    exp_digest: Optional[str],
    exp_subject: Optional[str],
) -> ApprovalResolutionResult:
    payload = matched_entry.get("payload") if isinstance(matched_entry.get("payload"), dict) else {}
    operator_id = matched_entry.get("operator_id") or payload.get("operator", {}).get("subject") or matched_entry.get("operator")

    synth_res = _check_synthetic_origin(matched_entry, payload, entry_id, ledger_str, operator_id)
    if synth_res:
        return synth_res

    decline_res = _check_operator_decline(matched_entry, payload, entry_id, ledger_str, operator_id)
    if decline_res:
        return decline_res

    mismatch_res = _check_digest_and_subject(matched_entry, payload, entry_id, ledger_str, exp_digest, exp_subject, operator_id)
    if mismatch_res:
        return mismatch_res

    actual_digest = matched_entry.get("entry_digest") or matched_entry.get("entry_sha256")
    actual_subject = matched_entry.get("subject_sha256") or payload.get("subject_sha256")

    if not verify_hitl_approval_entry(matched_entry):
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.INVALID_SIGNATURE,
            is_valid=False,
            is_rejected=False,
            is_unresolved=False,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason="INVALID_SIGNATURE: Ed25519 signature verification failed over canonical payload.",
            entry=matched_entry,
            operator_id=str(operator_id or ""),
            subject_sha256=actual_subject,
            entry_digest=actual_digest,
        )

    return ApprovalResolutionResult(
        status=ApprovalResolutionStatus.RESOLVED_VALID,
        is_valid=True,
        is_rejected=False,
        is_unresolved=False,
        entry_id=entry_id,
        ledger_path=ledger_str,
        reason=f"RESOLVED_VALID: Cryptographically verified authentic approval for '{entry_id}'.",
        entry=matched_entry,
        operator_id=str(operator_id or ""),
        subject_sha256=actual_subject,
        entry_digest=actual_digest,
        recorded_at=matched_entry.get("recorded_at") or matched_entry.get("timestamp"),
    )


def resolve_hitl_approval_reference(
    ref: Union[str, Dict[str, Any]],
    expected_subject_sha256: Optional[str] = None,
    expected_digest: Optional[str] = None,
    default_ledger_path: Optional[Union[str, Path]] = None,
) -> ApprovalResolutionResult:
    """Resolve an immutable HITL approval reference from its explicitly bound ledger.

    Distinguishes historical unresolvability from authentic operator rejection.
    """
    entry_id, explicit_ledger, exp_digest, exp_subject = _parse_ref_inputs(
        ref, expected_subject_sha256, expected_digest
    )

    if not entry_id:
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.UNRESOLVED_REFERENCE,
            is_valid=False,
            is_rejected=False,
            is_unresolved=True,
            entry_id="",
            ledger_path="",
            reason="UNRESOLVED: Empty or blank approval reference identifier.",
        )

    target_ledger = _resolve_target_ledger(explicit_ledger, default_ledger_path)
    ledger_str = str(target_ledger) if target_ledger else "<unresolved_ledger_path>"

    if target_ledger is None or not target_ledger.is_file():
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.LEDGER_UNAVAILABLE,
            is_valid=False,
            is_rejected=False,
            is_unresolved=True,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"LEDGER_UNAVAILABLE: Designated ledger not found at '{ledger_str}'.",
        )

    records, err = _load_ledger_records(target_ledger)
    if records is None:
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.LEDGER_UNAVAILABLE,
            is_valid=False,
            is_rejected=False,
            is_unresolved=True,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"LEDGER_UNAVAILABLE: Failed to parse ledger at '{ledger_str}': {err}",
        )

    matched_entry = next(
        (rec for rec in records if isinstance(rec, dict) and str(rec.get("entry_id") or rec.get("decision_id") or "").strip() == entry_id),
        None,
    )

    if matched_entry is None:
        return ApprovalResolutionResult(
            status=ApprovalResolutionStatus.UNRESOLVED_REFERENCE,
            is_valid=False,
            is_rejected=False,
            is_unresolved=True,
            entry_id=entry_id,
            ledger_path=ledger_str,
            reason=f"UNRESOLVED_REFERENCE: Entry '{entry_id}' not found in ledger '{ledger_str}'.",
        )

    return _validate_matched_entry(matched_entry, entry_id, ledger_str, exp_digest, exp_subject)
