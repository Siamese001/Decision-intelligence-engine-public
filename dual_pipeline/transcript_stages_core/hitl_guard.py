"""
dual_pipeline/transcript_stages_core/hitl_guard.py

Mandatory HITL (Human-In-The-Loop) Anti-Bypass Guard for Stage U0 Ingestion.
Enforces Master Invariant: HITL resolution is mandatory and CANNOT EVER be
bypassed, disabled, or skipped across any ingestion path originating from Stage U0.
Fails closed immediately if any bypass key is passed in metadata, options, or kwargs.
"""
from __future__ import annotations

import enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

FORBIDDEN_U0_HITL_BYPASS_KEYS = {
    "bypass_speaker_hitl",
    "bypass_hitl",
    "bypass_roster_gate",
    "no_hitl",
    "skip_hitl",
    "disable_hitl",
    "no_interactive",
    "no_interactive_hitl",
    "skip_speaker_attribution",
    "bypass_attribution",
    "bypass_attendee_gate",
    "bypass_attendee_confirmation",
    "skip_attendee_gate",
    "skip_attendee_confirmation",
    "attendees_confirmed_by_operator",
    "autonomous_if_decisive",
    "auto_approve_hitl",
    "mock_hitl",
    "roster_gate_cleared",
    "conformance_checked",
}

DISABLING_FLAG_NAMES = {
    "enable_hitl",
    "interactive_hitl",
    "require_hitl",
}


def _check_dict_entry(k: Any, v: Any, path: str, parent: Optional[Mapping] = None) -> None:
    key_str = str(k).lower().strip()
    loc = f" at '{path}.{k}'" if path else f" ('{k}')"

    if key_str == "attendees_confirmed_by_operator" and bool(v):
        receipt = parent.get("attendee_confirmation_receipt") if isinstance(parent, Mapping) else None
        if not (isinstance(receipt, Mapping) and receipt.get("status") == "RESOLVED_HITL"):
            raise ValueError(
                f"HITL cannot be removed, disabled, or bypassed in Stage U0 ingestion{loc}. "
                "Human-In-The-Loop resolution with atomic options and confidence levels is mandatory."
            )
        rec_subj = str(receipt.get("subject_sha256") or "").strip()
        if not rec_subj:
            raise ValueError(
                f"Attendee confirmation receipt missing subject_sha256 cryptographic binding{loc}. "
                "Receipt must be bound to the source audio hash or transcript digest."
            )
        source_hash = parent.get("source_file_hash") or parent.get("input_hash") if isinstance(parent, Mapping) else None
        if source_hash and rec_subj != "urn:nee:live-container-preflight-proof":
            clean_rec_subj = rec_subj.replace("urn:nee:source-file:sha256:", "").replace("urn:nee:unanchored-text:sha256:", "")
            candidate_hashes = [
                h for h in [
                    parent.get("source_file_hash"),
                    parent.get("input_hash"),
                    parent.get("subject_sha256"),
                    parent.get("context_sha256"),
                    parent.get("source_audio_hash"),
                    parent.get("audio_sha256"),
                ]
                if h
            ]
            matches_any = False
            for cand in candidate_hashes:
                clean_cand = str(cand).replace("urn:nee:source-file:sha256:", "").replace("urn:nee:unanchored-text:sha256:", "")
                if clean_cand == clean_rec_subj or clean_cand.startswith(clean_rec_subj) or clean_rec_subj.startswith(clean_cand):
                    matches_any = True
                    break
            if not matches_any:
                raise ValueError(
                    f"Attendee confirmation receipt subject_sha256 '{rec_subj}' does not match active source hash '{source_hash}'{loc}. "
                    "Stale or cross-meeting attendee receipts are strictly rejected."
                )
        if receipt.get("signature") and receipt.get("payload"):
            from dual_pipeline.hitl_governance_core.crypto import verify_hitl_approval_signature
            try:
                verify_hitl_approval_signature(receipt)
            except Exception as sig_err:
                raise ValueError(
                    f"Attendee confirmation receipt Ed25519 signature verification failed{loc}: {sig_err}"
                ) from sig_err
        return

    # Reject any forbidden bypass key set to truthy
    if key_str in FORBIDDEN_U0_HITL_BYPASS_KEYS and bool(v):
        raise ValueError(
            f"HITL cannot be removed, disabled, or bypassed in Stage U0 ingestion{loc}. "
            "Human-In-The-Loop resolution with atomic options and confidence levels is mandatory."
        )

    # Reject explicitly disabling HITL flags (e.g. interactive_hitl=False, enable_hitl=False)
    if key_str in DISABLING_FLAG_NAMES and v is False:
        raise ValueError(
            f"HITL cannot be disabled in Stage U0 ingestion{loc} ({k}=False). "
            "Human-In-The-Loop resolution with atomic options and confidence levels is mandatory."
        )

    if isinstance(v, (Mapping, Sequence)) and not isinstance(v, (str, bytes)):
        assert_no_u0_hitl_bypass(v, path=f"{path}.{k}" if path else str(k))


def assert_no_u0_hitl_bypass(data: Any, path: str = "") -> None:
    """
    Recursively inspects payloads, metadata, options, and kwargs for any attempt
    to remove, bypass, skip, or disable HITL in Stage U0 ingestion.
    Fails closed immediately if any bypass flag is present with a truthy value,
    or if any enable flag is set to False.
    """
    if isinstance(data, Mapping):
        for k, v in data.items():
            _check_dict_entry(k, v, path, parent=data)
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        for idx, item in enumerate(data):
            assert_no_u0_hitl_bypass(item, path=f"{path}[{idx}]")


class DiarizationDefenseReason(str, enum.Enum):
    """Authorized, governed reason codes permitting speaker diarization to be toggled off."""
    SINGLE_SPEAKER_CONFIRMED = "SINGLE_SPEAKER_CONFIRMED"
    DIARIZATION_FAILED_IRRECOVERABLY = "DIARIZATION_FAILED_IRRECOVERABLY"
    SOURCE_ALREADY_ATTRIBUTED = "SOURCE_ALREADY_ATTRIBUTED"


FORBIDDEN_DIARIZATION_OFF_KEYS = {
    "speaker_diarization",
    "diarization",
    "diarize",
    "enable_diarization",
    "require_diarization",
}


def validate_diarization_toggle(
    metadata: Dict[str, Any], options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Guarantees that speaker diarization is strictly ON by default.
    Disabling diarization requires an authorized, defended reason code.
    If an operator or caller attempts to disable diarization without a valid reason code,
    this function raises ValueError and enforces diarization to ON.
    """
    opts = options or {}
    status_val = str(metadata.get("diarization_status", "")).upper().strip()
    is_status_off = status_val in ("OFF", "DISABLED", "FALSE")

    is_flag_off = False
    for k in FORBIDDEN_DIARIZATION_OFF_KEYS:
        val = metadata.get(k)
        if val is False or (isinstance(val, str) and val.lower().strip() in ("false", "off", "disabled", "0")):
            is_flag_off = True
            break
        val_opt = opts.get(k)
        if val_opt is False or (isinstance(val_opt, str) and val_opt.lower().strip() in ("false", "off", "disabled", "0")):
            is_flag_off = True
            break

    if is_status_off or is_flag_off:
        reason = (
            metadata.get("diarization_off_reason")
            or metadata.get("diarization_defense_code")
            or opts.get("diarization_off_reason")
            or opts.get("diarization_defense_code")
        )
        if not reason:
            raise ValueError(
                "Speaker diarization is enforced ON by default and cannot be disabled without an authorized, defended reason code. "
                "To request diarization=OFF, you must supply 'diarization_off_reason' with one of: "
                f"{[r.value for r in DiarizationDefenseReason]}."
            )
        reason_str = str(reason).strip()
        allowed = {r.value for r in DiarizationDefenseReason}
        if reason_str not in allowed:
            raise ValueError(
                f"Invalid diarization defense reason '{reason_str}'. "
                f"Authorized defense codes are: {sorted(allowed)}."
            )
        metadata["diarization_defense_code"] = reason_str
        metadata["diarization_defense_verified"] = True
        metadata["diarization_status"] = "UNDIARIZED"
    else:
        # Default to ON
        metadata.setdefault("speaker_diarization", True)

    return metadata


def assert_chat_attributions_confirmed(
    attributions_path: Optional[Union[Path, str]] = None,
    attributions: Optional[Sequence[Any]] = None,
) -> None:
    """Fail closed if inline chat attributions exist but have not been
    confirmed through the governed HITL ask_user_question confirmation pathway.

    Raises ValueError if:
    - inline_chat_attributions.json exists with PENDING records
    - Any attribution in attributions list is PENDING or lacks terminal confirmation
    """
    records = []
    if attributions is not None:
        records = list(attributions)
    elif attributions_path:
        p = Path(attributions_path)
        if p.is_file():
            import json
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                records = raw if isinstance(raw, list) else []
            except Exception as e:
                raise RuntimeError(
                    f"Fail-Fast: Failed parsing inline chat attributions file {p}: {e}"
                ) from e

    if not records:
        return

    pending = []
    for r in records:
        status = r.confirmation_status if hasattr(r, "confirmation_status") else r.get("confirmation_status")
        if status == "PENDING" or not status:
            pending.append(r)

    if pending:
        raise ValueError(
            f"CHAT_ATTRIBUTION_HITL_INCOMPLETE: {len(pending)} inline chat "
            "attribution(s) remain unconfirmed. All inline speaker attributions "
            "must be explicitly validated by the human operator via ask_user_question "
            "before authoritative 01_* transcript generation can proceed."
        )
