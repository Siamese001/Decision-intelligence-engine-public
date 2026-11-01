"""
dual_pipeline/transcript_qa_core/gate1_alr.py

Ambiguity Lock Register (ALR) and cryptographic receipt validation for Gate 1.7.
Guarantees that any '(Operator Verified)' claim or collapse obligation is strictly
bound to an authentic Ed25519-signed entry in hitl_decision_ledger.json.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.hitl_governance_core.crypto import (
    compute_alr_subject_digest,
    export_public_key_pem,
    find_signed_approvals,
    get_governed_hitl_keypair,
    verify_hitl_approval_signature,
)
from dual_pipeline.hitl_governance_core.ledger import _find_default_ledger_path


from .gate1_helpers import (
    _extract_all_turns,
    _extract_roster_names,
    _is_generic_speaker,
    _is_single_speaker_session,
)

logger = logging.getLogger(__name__)

OPERATOR_VERIFIED_PATTERNS = [
    re.compile(r"\b(?:operator[\s\-]+verified|operator[\s\-]+confirmed|human[\s\-]+operator[\s\-]+verified|verified[\s\-]+by[\s\-]+operator)\b", re.IGNORECASE),
    re.compile(r"\b(?:cryptographically[\s\-]+verified[\s\-]+operator[\s\-]+approval|operator[\s\-]+approval|human[\s\-]+operator[\s\-]+approval)\b", re.IGNORECASE),
    re.compile(r"\b(?:operator[\s\-]+approved|human[\s\-]+verified|human[\s\-]+confirmed)\b", re.IGNORECASE),
    re.compile(r"\(\s*operator[\s\-]+verified\s*\)", re.IGNORECASE),
    re.compile(r"\[\s*operator[\s\-]+verified\s*\]", re.IGNORECASE),
]


def _extract_alr_table_rows(f1_text: str) -> List[str]:
    """Extract table rows from the Ambiguity Lock Register section."""
    alr_match = re.search(
        r"(?:^|\n)#{1,3}\s+Ambiguity Lock Register\b[\s\S]*?(?=\n#{1,3}\s+|\Z)",
        f1_text,
        re.IGNORECASE,
    )
    if not alr_match:
        return []
    alr_body = alr_match.group(0)
    lines = [line.strip() for line in alr_body.splitlines() if line.strip()]
    table_rows = []
    for line in lines:
        if line.startswith("|") and line.endswith("|"):
            if re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", line):
                continue
            table_rows.append(line)
    return table_rows


def _contains_speaker_attribution_grounding(rows: List[str], roster_names: List[str]) -> bool:
    """Determine if ALR rows contain substantive speaker attribution grounding."""
    attribution_keywords = (
        "speaker attribution", "attribution", "acoustic", "room mic",
        "crosstalk", "participant", "diarization", "voice profile",
        "separation", "operator confirmed (hitl", "hitl amb_",
    )
    combined_rows = " ".join(rows).lower()
    if any(kw in combined_rows for kw in attribution_keywords):
        return True
    for name in roster_names:
        first_name = name.lower().split()[0]
        if first_name in combined_rows and len(first_name) >= 3:
            return True
    return False


def _check_generic_turn_ratio(
    f1_text: str,
    roster_names: List[str],
) -> List[str]:
    """Return failure strings if >15% of turns carry generic/un-attributed speaker labels."""
    turns = _extract_all_turns(f1_text)
    if not turns:
        return []
    gen_cnt = sum(1 for _, spk, _ in turns if _is_generic_speaker(spk))
    ratio = gen_cnt / len(turns)
    if gen_cnt == len(turns) or ratio > 0.15:
        return [
            f"AMBIGUITY_LOCK_REGISTER_DEGENERATE: Ambiguity Lock Register claims speaker attribution, "
            f"but Output 1 body contains {gen_cnt}/{len(turns)} ({int(ratio * 100)}%) "
            "generic un-attributed speaker turns."
        ]
    return []


def _has_placeholder_role(role: str) -> bool:
    """Uses canonical _is_placeholder check for Gate 1.7 receipt binding."""
    from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import (
        _is_placeholder,
    )
    return _is_placeholder(role)


def _check_hitl_receipt_binding(
    meta: Dict[str, Any],
    roster_names: List[str],
    f1_text: str = "",
) -> List[str]:
    """Return failure strings when placeholder roles exist but no authentic HITL receipt was recorded."""
    pre_enrichment_roles = dict(meta.get("pre_enrichment_roles", {}))
    autonomous_decisions = meta.get("autonomous_hitl_role_decisions", {})
    hitl_receipts = meta.get("hitl_role_receipts", {})

    # If pre_enrichment_roles is not present in meta, infer from f1_text
    if not pre_enrichment_roles and f1_text:
        for name in roster_names:
            pat = re.compile(rf"{re.escape(name)}\s*--\s*[^(\n]*\(([^)]+)\)", re.IGNORECASE)
            m = pat.search(f1_text)
            if m:
                role_found = m.group(1).strip()
                if _has_placeholder_role(role_found) or "title not provided" in role_found.lower() or "role not listed" in role_found.lower():
                    pre_enrichment_roles[name] = role_found

    placeholder_names = [
        name for name, role in pre_enrichment_roles.items()
        if (_has_placeholder_role(role) or "title not provided" in str(role).lower() or "role not listed" in str(role).lower())
        and name not in hitl_receipts
    ]
    if placeholder_names and not autonomous_decisions and not meta.get("attendee_roles_confirmed_by_operator"):
        return [
            f"AMBIGUITY_LOCK_REGISTER_NOT_HITL_BACKED: ALR contains speaker attribution entries, "
            f"but no HITL role decisions were recorded for attendees with placeholder roles "
            f"({', '.join(placeholder_names)}). This indicates LLM-fabricated attribution "
            f"without operator grounding. Provide explicit roles or resolve via HITL."
        ]
    return []


UNRECONCILED_METRIC_PATTERNS = [
    re.compile(r"differing figures", re.IGNORECASE),
    re.compile(r"conflicting contemporaneous statements", re.IGNORECASE),
    re.compile(r"not reconciled into one definitive count", re.IGNORECASE),
]


def _check_unreconciled_metric_discrepancies(f1_text: str, meta: Dict[str, Any]) -> List[str]:
    """Ensure that ALR does not passively dump unresolved metric discrepancies without HITL receipt."""
    if meta.get("metrics_confirmed_by_operator") or meta.get("bypass_metric_discrepancy_gate"):
        return []

    metric_receipts = meta.get("hitl_metric_receipts", {})
    authoritative_metrics = meta.get("authoritative_metrics", {})
    if metric_receipts or authoritative_metrics:
        return []

    for pat in UNRECONCILED_METRIC_PATTERNS:
        if pat.search(f1_text):
            return [
                "AMBIGUITY_LOCK_REGISTER_UNRECONCILED_METRICS: Output 1 Ambiguity Lock Register contains "
                "unreconciled conflicting metric numbers (e.g. differing figures / conflicting contemporaneous statements) "
                "without an authentic operator HITL resolution receipt. Authoritative metric baseline must be verified via HITL."
            ]
    return []


def _detect_operator_verified_claims(f1_text: str, alr_rows: List[str]) -> List[str]:
    """Detect claims of human operator verification in ALR table rows or Output 1 text."""
    claimed: List[str] = []
    for row in alr_rows:
        for pat in OPERATOR_VERIFIED_PATTERNS:
            if pat.search(row):
                claimed.append(row)
                break
    if not claimed:
        alr_match = re.search(
            r"(?:^|\n)#{1,3}\s+Ambiguity Lock Register\b[\s\S]*?(?=\n#{1,3}\s+|\Z)",
            f1_text,
            re.IGNORECASE,
        )
        if alr_match:
            alr_body = alr_match.group(0)
            for pat in OPERATOR_VERIFIED_PATTERNS:
                m = pat.search(alr_body)
                if m:
                    claimed.append(m.group(0))
    return claimed


def _has_unresolved_collapse_obligation(meta: Dict[str, Any]) -> bool:
    """Determine whether the meeting lineage carries an active single-speaker collapse obligation."""
    if meta.get("single_speaker_collapse") or meta.get("collapse_obligation"):
        return True
    if meta.get("speaker_skew_detected"):
        return True
    required = meta.get("required_case_ids") or []
    return any(c in ("SC-001", "single-speaker-collapse", "speaker_collapse") for c in required)


def _resolve_expected_subject_digest(
    meta: Dict[str, Any],
    roster_names: List[str],
) -> Optional[str]:
    """Resolve expected subject SHA-256 digest from metadata or canonical meeting identity."""
    expected = meta.get("subject_sha256") or meta.get("context_sha256")
    if not expected and isinstance(meta.get("subject"), dict):
        expected = compute_alr_subject_digest(meta["subject"])
    if not expected:
        m_name = meta.get("meeting_name")
        m_date = meta.get("date") or meta.get("meeting_date")
        m_proj = meta.get("project", "nea")
        if m_name and m_date and roster_names:
            subject_dict = {
                "meeting_name": str(m_name),
                "meeting_date": str(m_date),
                "project": str(m_proj),
                "attendees": sorted(roster_names),
            }
            expected = compute_alr_subject_digest(subject_dict)
            meta["subject_sha256"] = expected
            meta["context_sha256"] = expected
            meta["subject"] = subject_dict
    return expected


def _clean_slug(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s).lower().strip("_")


def _is_matching_approval_entry(
    entry: Dict[str, Any],
    expected_subject: str,
    meta: Dict[str, Any],
    now_dt: datetime,
) -> bool:
    payload = entry.get("payload", {})
    if payload.get("decision") != "APPROVE":
        return False

    op_info = payload.get("operator", {})
    op_id = (op_info.get("subject") if isinstance(op_info, dict) else None) or entry.get("operator_id") or ""
    from dual_pipeline.hitl_governance_core.scoped_autonomy import is_autonomous_alias
    if not op_id or is_autonomous_alias(op_id) or "engine" in str(op_id).lower() or op_id in ("mock_operator", "fake_operator"):
        return False

    subj = payload.get("subject_sha256") or entry.get("subject_sha256")
    if subj != expected_subject:
        return False

    expected_slug = meta.get("meeting_slug") or meta.get("meeting_name")
    entry_slug = payload.get("meeting_slug") or entry.get("meeting_slug") or payload.get("metadata", {}).get("meeting_name")
    if expected_slug and entry_slug and _clean_slug(expected_slug) != _clean_slug(entry_slug):
        return False

    sig = entry.get("signature", "")
    pub_pem = entry.get("public_key_pem")
    if not pub_pem:
        try:
            _, gov_pub = get_governed_hitl_keypair()
            pub_pem = export_public_key_pem(gov_pub)
        except Exception:
            pub_pem = None

    if not pub_pem or not sig or not verify_hitl_approval_signature(payload, sig, pub_pem):
        return False

    expires_at = payload.get("expires_at") or entry.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if now_dt > exp_dt:
                return False
        except (ValueError, TypeError):
            return False

    return True


def _find_matching_verified_approval(
    all_approvals: List[Dict[str, Any]],
    expected_subject: str,
    meta: Dict[str, Any],
    now_dt: datetime,
) -> Optional[Dict[str, Any]]:
    for entry in all_approvals:
        if _is_matching_approval_entry(entry, expected_subject, meta, now_dt):
            return entry
    return None


def verify_alr_cryptographic_receipt(
    f1_text: str,
    meta: Dict[str, Any],
    ledger_path: Optional[Union[Path, str]] = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Cryptographically verify ALR '(Operator Verified)' claims or collapse obligations against the ledger.

    CRITICAL SECURITY INVARIANT:
    Caller assertions of `hitl_receipt_bound: True` are strictly discarded.
    The verifier alone computes this status by independently verifying Ed25519
    signatures over RFC 8785 (JCS) canonical payload bytes.
    """
    alr_rows = _extract_alr_table_rows(f1_text)
    claims = _detect_operator_verified_claims(f1_text, alr_rows)
    has_collapse_obligation = _has_unresolved_collapse_obligation(meta)

    roster_names = _extract_roster_names(meta, f1_text)
    placeholder_failures = _check_hitl_receipt_binding(meta, roster_names, f1_text)

    if not claims and not has_collapse_obligation:
        is_bound = bool(meta.get("autonomous_hitl_role_decisions")) and not placeholder_failures
        return len(placeholder_failures) == 0, {
            "hitl_receipt_bound": is_bound,
            "verification_type": "none_required",
            "claims_detected": [],
        }, placeholder_failures

    target_ledger = Path(ledger_path).resolve() if ledger_path else (
        Path(meta["hitl_ledger_path"]).resolve() if meta.get("hitl_ledger_path") else _find_default_ledger_path().resolve()
    )

    expected_subject = _resolve_expected_subject_digest(meta, roster_names)

    if not expected_subject:
        failures: List[str] = list(placeholder_failures)
        if claims:
            failures.append(
                "UNBINDABLE_SUBJECT: Ambiguity Lock Register asserts '(Operator Verified)' "
                f"({len(claims)} claim instances detected), but metadata contains no 'subject_sha256' "
                "or 'context_sha256' to bind the cryptographic receipt. Verification fails closed."
            )
        elif has_collapse_obligation:
            failures.append(
                "UNBINDABLE_SUBJECT: Lineage has active single-speaker collapse obligation, "
                "but metadata contains no 'subject_sha256' or 'context_sha256' to bind the cryptographic receipt. "
                "Verification fails closed."
            )
        return False, {
            "hitl_receipt_bound": False,
            "verification_type": "failed",
            "claims_detected": claims,
            "expected_subject_sha256": None,
        }, failures

    all_approvals = find_signed_approvals(ledger_path=target_ledger)
    now_dt = datetime.now(timezone.utc)
    verified_entry = _find_matching_verified_approval(
        all_approvals, expected_subject, meta, now_dt
    )

    if verified_entry:
        passed = len(placeholder_failures) == 0
        return passed, {
            "hitl_receipt_bound": True,
            "verification_type": "ed25519_jcs",
            "verified_entry_id": verified_entry.get("entry_id") or verified_entry.get("decision_id"),
            "operator_id": verified_entry.get("operator_id") or verified_entry.get("payload", {}).get("operator", {}).get("subject"),
            "subject_sha256": verified_entry.get("subject_sha256"),
            "claims_detected": claims,
        }, placeholder_failures

    failures: List[str] = list(placeholder_failures)
    if claims:
        failures.append(
            "UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM: Ambiguity Lock Register asserts "
            f"'(Operator Verified)' ({len(claims)} claim instances detected), but no "
            "cryptographically valid Ed25519 signed ledger receipt exists in hitl_decision_ledger.json."
        )
    elif has_collapse_obligation:
        failures.append(
            "UNRESOLVED_COLLAPSE_OBLIGATION: Lineage has active single-speaker collapse obligation "
            "requiring a cryptographically verified operator approval receipt in hitl_decision_ledger.json "
            "before Gate 1.7 can pass."
        )

    return False, {
        "hitl_receipt_bound": False,
        "verification_type": "failed",
        "claims_detected": claims,
        "expected_subject_sha256": expected_subject,
    }, failures


def check_ambiguity_lock_register_and_speaker_hitl(
    f1_text: str,
    meta: Dict[str, Any],
    ledger_path: Optional[Union[Path, str]] = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Audit Output 1 for an itemized Ambiguity Lock Register with cryptographic speaker attribution HITL.

    Gate 1.7 Invariants:
    1. '## Ambiguity Lock Register' section must be present.
    2. Must contain an itemized table (not empty or single-line placeholder).
    3. Multi-participant meetings must include explicit speaker attribution grounding.
    4. Any '(Operator Verified)' claim or collapse obligation MUST be backed by a verified Ed25519 signature.
    """
    from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass
    assert_no_u0_hitl_bypass(meta)
    roster_names = _extract_roster_names(meta, f1_text)
    table_rows = _extract_alr_table_rows(f1_text)
    data_rows = table_rows[1:] if len(table_rows) > 1 else []
    has_alr_header = bool(re.search(r"(?:^|\n)#{1,3}\s+Ambiguity Lock Register\b", f1_text, re.IGNORECASE))

    claims = _detect_operator_verified_claims(f1_text, data_rows)
    if _is_single_speaker_session(f1_text, roster_names) and not claims and not _has_unresolved_collapse_obligation(meta):
        return True, {
            "present": has_alr_header,
            "item_count": len(data_rows),
            "speaker_attribution_grounded": True,
            "single_speaker": True,
            "hitl_receipt_bound": False,
        }, []

    failures: List[str] = []
    if not has_alr_header:
        failures.append(
            "AMBIGUITY_LOCK_REGISTER_MISSING: Output 1 is missing canonical '###### Ambiguity Lock Register' section."
        )
        return False, {"present": False, "item_count": 0, "speaker_attribution_grounded": False, "hitl_receipt_bound": False}, failures

    degenerate_match = re.search(
        r"No ambiguity met the HITL threshold|All diarized speakers successfully validated|None.",
        f1_text,
        re.IGNORECASE,
    )
    if not data_rows and degenerate_match:
        failures.append(
            "AMBIGUITY_LOCK_REGISTER_DEGENERATE: Ambiguity Lock Register contains placeholder text without an itemized grounding table."
        )
        return False, {"present": True, "item_count": 0, "speaker_attribution_grounded": False, "hitl_receipt_bound": False}, failures

    if not data_rows:
        failures.append(
            "AMBIGUITY_LOCK_REGISTER_EMPTY: Ambiguity Lock Register contains no itemized markdown table entries."
        )
        return False, {"present": True, "item_count": 0, "speaker_attribution_grounded": False, "hitl_receipt_bound": False}, failures

    spk_grounded = _contains_speaker_attribution_grounding(data_rows, roster_names)

    if len(roster_names) >= 2:
        if not spk_grounded:
            failures.append(
                f"AMBIGUITY_LOCK_REGISTER_SPEAKER_HITL_MISSING: Output 1 Ambiguity Lock Register lacks itemized speaker attribution "
                f"grounding entries for meeting with {len(roster_names)} attendees ({', '.join(roster_names)})."
            )
        else:
            failures.extend(_check_generic_turn_ratio(f1_text, roster_names))

    crypto_passed, crypto_details, crypto_failures = verify_alr_cryptographic_receipt(f1_text, meta, ledger_path)
    failures.extend(crypto_failures)
    failures.extend(_check_hitl_receipt_binding(meta, roster_names, f1_text))
    failures.extend(_check_unreconciled_metric_discrepancies(f1_text, meta))

    passed = len(failures) == 0
    details = {
        "present": True,
        "item_count": len(data_rows),
        "speaker_attribution_grounded": spk_grounded,
        "rows_extracted": len(table_rows),
        "hitl_receipt_bound": crypto_details.get("hitl_receipt_bound", False),
        "verification_type": crypto_details.get("verification_type"),
        "verified_entry_id": crypto_details.get("verified_entry_id"),
        "crypto_verification": crypto_details,
    }
    return passed, details, failures


def check_alr_row_count_minimum(
    f1_text: str,
    meta: Dict[str, Any],
) -> Tuple[bool, int, int, List[str]]:
    """Gate 1.9: ALR must have >= (number of distinct speakers + 1) rows."""
    roster_names = _extract_roster_names(meta, f1_text)
    table_rows = _extract_alr_table_rows(f1_text)
    data_rows = table_rows[1:] if len(table_rows) > 1 else []

    n_speakers = len(roster_names)
    min_rows = max(1, n_speakers + 1)
    actual_rows = len(data_rows)

    if n_speakers == 0 or _is_single_speaker_session(f1_text, roster_names):
        return True, actual_rows, min_rows, []

    failures: List[str] = []
    if actual_rows < min_rows:
        failures.append(
            f"AMBIGUITY_LOCK_REGISTER_INSUFFICIENT_ROWS: ALR has {actual_rows} data rows "
            f"but requires >= {min_rows} ({n_speakers} attendees + 1 lexicon). "
            f"Each roster attendee must have an explicit speaker attribution grounding entry."
        )
    return len(failures) == 0, actual_rows, min_rows, failures
