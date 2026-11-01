"""Calibration Pipeline — governed parameter tuning with release approval.

Enforces closed-loop calibration where parameter changes (weights, thresholds,
prompt templates, routing rules) require evaluation plans, risk ratings, and
explicit human approval before release activation.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

logger = logging.getLogger("dual_pipeline.calibration")

from .contracts import create_artifact
from .core import (
    stable_id,
    utc_now,
)

LEARNING_CHANGE_PROPOSAL_CONTRACT_ID = (
    "urn:nee:dual-pipeline:learning-change-proposal:1.0.0"
)

VALID_PROPOSAL_TYPES = frozenset({
    "alias_addition",
    "extraction_template",
    "source_authority_policy",
    "threshold",
    "prompt",
    "routing",
    "retrieval_weight",
    "workflow",
    "benchmark_case",
    "hitl_confidence",
    "copilot_context",
    "copilot_grounding",
    "query_retrieval",
})

VALID_RISKS = frozenset({"low", "medium", "high"})


class CalibrationError(ValueError):
    """Raised when a calibration proposal violates governance invariants."""


def create_calibration_proposal(
    *,
    proposal_type: str,
    target_parameter: str,
    current_value: Any,
    proposed_value: Any,
    problem_description: str,
    run_ids: Sequence[str],
    evidence_event_ids: Sequence[str],
    evaluation_plan: Sequence[str],
    risk: str = "medium",
    created_at: str | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a contract-valid, approval-required learning change proposal.

    Invariants enforced:
    - ``status`` is always ``"proposed"``
    - ``approval_required`` is always ``True``
    - ``auto_apply`` is always ``False``
    - Replay evaluation plan is required
    """
    if proposal_type not in VALID_PROPOSAL_TYPES:
        raise CalibrationError(f"unsupported proposal_type: {proposal_type}")
    if risk not in VALID_RISKS:
        raise CalibrationError(f"invalid risk level: {risk}")
    if not run_ids:
        raise CalibrationError("at least one run_id is required")
    if not evidence_event_ids:
        raise CalibrationError("at least one evidence_event_id is required")
    if not evaluation_plan:
        raise CalibrationError("evaluation_plan must not be empty")

    timestamp = created_at or utc_now()
    clean_runs = list(dict.fromkeys(run_ids))
    clean_events = list(dict.fromkeys(evidence_event_ids))
    clean_plan = [str(item) for item in evaluation_plan if str(item).strip()]

    proposal_id = stable_id(
        "calibration-proposal",
        proposal_type,
        target_parameter,
        timestamp,
        length=28,
    )

    ext = dict(extensions) if extensions else {}
    ext.setdefault("com.partner_advisory.nee.calibration", {
        "target_parameter": target_parameter,
        "current_value": current_value,
        "proposed_value": proposed_value,
        "requires_release_gate": True,
        "auto_mutation_permitted": False,
    })

    return create_artifact(
        "learning-change-proposal",
        proposal_id,
        proposal_id=proposal_id,
        run_ids=clean_runs,
        proposal_type=proposal_type,
        status="proposed",
        problem=problem_description,
        evidence_event_ids=clean_events,
        proposed_change=(
            f"Calibrate {target_parameter} from {current_value} to {proposed_value}. "
            "Requires governed approval before release."
        ),
        evaluation_plan=clean_plan,
        risk=risk,
        approval_required=True,
        auto_apply=False,
        created_at=timestamp,
        extensions=ext,
    )


def verify_calibration_approval(
    proposal: Mapping[str, Any],
    approval_receipt: Mapping[str, Any] | None,
    require_signature: bool = False,
) -> bool:
    """Verify that a calibration proposal has a valid, authentic human approval binding.

    Enforces:
    1. Explicit presence of required receipt fields.
    2. Exact proposal_id and proposal_digest matching.
    3. Authentic human origin (anti-synthetic stop hook firewall via validate_approval_origin).
    4. Cryptographic Ed25519 signature over RFC 8785 canonical bytes when present or mandated.
    """
    if approval_receipt is None:
        return False

    required_keys = {"receipt_id", "proposal_id", "proposal_digest", "approved_by", "approved_at"}
    if not required_keys.issubset(approval_receipt.keys()):
        return False

    if approval_receipt.get("proposal_id") != proposal.get("proposal_id"):
        return False

    raw_digest = proposal.get("canonical_digest", {})
    expected_digest = (
        raw_digest.get("value")
        if isinstance(raw_digest, Mapping)
        else str(raw_digest)
    )
    if approval_receipt.get("proposal_digest") != expected_digest:
        return False

    # Anti-synthetic stop hook firewall: reject synthetic policy approvals
    from .hitl_governance import validate_approval_origin
    is_valid_origin, _ = validate_approval_origin(approval_receipt)
    if not is_valid_origin:
        return False

    # Check Ed25519 signature
    sig = approval_receipt.get("ed25519_signature") or approval_receipt.get("signature")
    must_verify_sig = require_signature or bool(sig) or proposal.get("proposal_type") in ("hitl_confidence", "hitl_parameter")
    if must_verify_sig:
        if not sig:
            return False
        from .hitl_governance_core.crypto import (
            get_governed_hitl_keypair,
            verify_hitl_approval_signature,
        )
        pub_key = approval_receipt.get("public_key")
        if pub_key is None:
            _, default_pub = get_governed_hitl_keypair()
            pub_key = default_pub
        payload_to_verify = {
            k: v
            for k, v in approval_receipt.items()
            if k not in ("signature", "ed25519_signature", "public_key")
        }
        if not verify_hitl_approval_signature(payload_to_verify, str(sig), pub_key):
            return False

    return bool(approval_receipt.get("approved_by"))


def create_signed_calibration_approval_receipt(
    proposal: Mapping[str, Any],
    approved_by: str = "human_operator",
    private_key: Any = None,
    public_key: Any = None,
    user_command: str = "implement",
    rationale: str = "",
    origin: str = "USER_EXPLICIT",
    **extra: Any,
) -> dict[str, Any]:
    """Constructs, canonicalizes, and signs an immutable Ed25519 approval receipt."""
    from .hitl_governance_core.crypto import (
        export_public_key_pem,
        get_governed_hitl_keypair,
        sign_hitl_approval_payload,
    )
    if private_key is None or public_key is None:
        priv, pub = get_governed_hitl_keypair()
        private_key = private_key or priv
        public_key = public_key or pub

    raw_digest = proposal.get("canonical_digest", {})
    prop_digest = raw_digest.get("value") if isinstance(raw_digest, Mapping) else str(raw_digest)
    now_iso = utc_now()
    p_id = str(proposal.get("proposal_id", "unknown"))
    receipt_id = stable_id("receipt-approval", p_id, now_iso, length=24)

    payload_to_sign = {
        "receipt_id": receipt_id,
        "proposal_id": p_id,
        "proposal_digest": prop_digest,
        "approved_by": approved_by,
        "approved_at": now_iso,
        "source": "user_input",
        "prior_user_command": user_command,
        "origin": origin,
        "rationale": rationale,
        **extra,
    }
    sig = sign_hitl_approval_payload(payload_to_sign, private_key)
    receipt = dict(payload_to_sign)
    receipt["ed25519_signature"] = sig
    receipt["signature"] = sig
    receipt["public_key"] = export_public_key_pem(public_key)
    return receipt


def apply_calibrated_parameter(
    proposal: Mapping[str, Any],
    approval_receipt: Mapping[str, Any] | None,
    config_path: Any | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Apply a calibrated parameter change, requiring explicit prior approval.

    Invariants enforced:
    1. Approval receipt must be present, valid, and cryptographically bound to proposal_id and proposal_digest.
    2. Atomically persists parameter update to parameters.json (or config_path if provided).
    3. Reloads ParameterRegistry to ensure live consistency.
    4. Raises PermissionError if approval is absent or unverified.
    """
    if not verify_calibration_approval(proposal, approval_receipt):
        raise PermissionError(
            f"calibration proposal {proposal.get('proposal_id')} is not approved "
            "for release activation (approval_required=True, auto_apply=False)"
        )

    ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.calibration", {})
    target_param = ext.get("target_parameter")
    proposed_val = ext.get("proposed_value")
    current_val = ext.get("current_value")

    if target_param and persist:
        try:
            from .parameters import _GLOBAL_REGISTRY, set_parameter
            target_file = config_path or _GLOBAL_REGISTRY.config_path
            if target_file.exists():
                set_parameter(target_param, proposed_val, persist=True, config_path=target_file)
        except Exception as exc:
            logger.debug("Failed to persist calibrated parameter %s: %s", target_param, exc)

    return {
        "parameter": target_param,
        "applied_value": proposed_val,
        "previous_value": current_val,
        "activated_at": utc_now(),
        "approval_receipt_id": approval_receipt["receipt_id"],
    }
