"""
dual_pipeline/hitl_governance_core/decision_clearance_core/verifier.py

Unified exact-scope read-only verifier for operator decisions and autonomous resolutions.
Enforces:
- Exact decision ID matching (zero SQL wildcards or prefix matching).
- Scope binding (wave, run_id, worktree, options content).
- Fail-closed store integrity (corrupt SQLite never falls back to stale JSON).
- Strict human-only rejection of autonomous authority where mandated.
- Read-only execution (zero side-effect writes).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.hitl_governance_core.decision_record_writer import verify_chain_integrity
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from storage.connection import create_sqlite_conn

from .legacy_adapter import normalize_legacy_record
from .models import (
    ClearanceAuthority,
    ClearanceVerificationRequest,
    ClearanceVerificationResult,
    PreWaveDecision,
)


def _check_sqlite_integrity_safely(sqlite_path: Path) -> Tuple[bool, str]:
    if not sqlite_path.is_file():
        return True, "No SQLite file present."
    try:
        conn = create_sqlite_conn(sqlite_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hitl_decisions'")
        has_table = cur.fetchone() is not None
        conn.close()
        if not has_table:
            return True, "hitl_decisions table not present."
        return verify_chain_integrity(sqlite_path)
    except Exception as exc:
        return False, f"Integrity check raised exception: {exc}"


def _verify_human_record_row(
    row: Tuple[Any, ...],
    req: ClearanceVerificationRequest,
) -> Optional[ClearanceVerificationResult]:
    seq, d_digest, op_id, meta_raw, found_id, sel_opt = row
    meta: Dict[str, Any] = {}
    if meta_raw:
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
        except Exception:
            meta = {}

    origin = meta.get("approval_origin") or meta.get("origin_verification") or op_id
    is_valid, origin_msg = validate_approval_origin(origin)
    if not is_valid:
        return ClearanceVerificationResult(
            cleared=False, authority_type=ClearanceAuthority.HUMAN, status_code="REJECTED_SYNTHETIC",
            reason=f"SYNTHETIC_APPROVAL_REJECTED: {origin_msg}", record_id=str(seq), digest=d_digest, operator_id=op_id,
        )

    # Outcome check
    selected = str(sel_opt or meta.get("selected_option") or "").upper()
    outcome = str(meta.get("outcome_type") or "").upper()
    if selected in ("DECLINED", "CANCELLED", "OPT_DECLINE") or outcome == "OVERRIDDEN":
        return ClearanceVerificationResult(
            cleared=False, authority_type=ClearanceAuthority.HUMAN, status_code="REJECTED_DECLINED",
            reason="DECLINED: Decision was explicitly declined by human operator.", record_id=str(seq), digest=d_digest, operator_id=op_id,
        )

    # Scope validation (wave check)
    rec_wave = meta.get("wave")
    if req.wave is not None and rec_wave is not None and int(rec_wave) != int(req.wave):
        return ClearanceVerificationResult(
            cleared=False, authority_type=ClearanceAuthority.HUMAN, status_code="REJECTED_SCOPE_MISMATCH",
            reason=f"SCOPE_MISMATCH: Record was approved for Wave {rec_wave}, but verification requires Wave {req.wave}.",
            record_id=str(seq), digest=d_digest, operator_id=op_id,
        )

    return ClearanceVerificationResult(
        cleared=True, authority_type=ClearanceAuthority.HUMAN, status_code="CLEARED_HUMAN",
        reason=f"Verified authentic human operator ratification (seq: {seq}, digest: {d_digest[:16]}...)",
        record_id=str(seq), digest=d_digest, operator_id=op_id,
    )


def _query_human_sqlite_store(
    sqlite_path: Path,
    req: ClearanceVerificationRequest,
) -> Tuple[Optional[ClearanceVerificationResult], bool]:
    """Queries human SQLite store with exact ID matching. Returns (result, table_exists)."""
    conn = create_sqlite_conn(sqlite_path, readonly=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hitl_decisions'"
        )
        if not cur.fetchone():
            return None, False

        # STRICT EXACT MATCHING ONLY: NO LIKE or prefix matching
        cur.execute(
            """
            SELECT sequence_number, entry_digest, operator_id, metadata, decision_id, selected_option
            FROM hitl_decisions
            WHERE decision_id = ?
            ORDER BY sequence_number DESC LIMIT 1
            """,
            (req.decision_id,),
        )
        row = cur.fetchone()
        if row:
            res = _verify_human_record_row(row, req)
            return res, True
        return None, True
    finally:
        conn.close()


def _verify_autonomous_sqlite_row(
    row: Tuple[Any, ...],
    req: ClearanceVerificationRequest,
) -> ClearanceVerificationResult:
    (
        res_id, seq_num, dec_id, rev, wt, run_id, wave, target, auth_type,
        cat, idemp_key, sel_opt_id, sel_opt_json, opts_json, margin, entropy,
        top_conf, sec_conf, pol_digest, pol_snap_json, dec_content_digest,
        prev_hash, rec_at, signer_id, sig_b64, res_digest
    ) = row

    if req.require_human_only:
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=ClearanceAuthority.AUTONOMOUS,
            status_code="REJECTED_HUMAN_REQUIRED",
            reason="HUMAN_AUTHORITY_REQUIRED: Ingestion mutations and sensitive actions prohibit autonomous clearance.",
            record_id=res_id,
            digest=res_digest,
        )

    if req.enforce_rollout_switch:
        from dual_pipeline.hitl_governance_core.autonomous_resolution.rollout import (
            is_autonomous_consumption_allowed,
        )

        if not is_autonomous_consumption_allowed():
            return ClearanceVerificationResult(
                cleared=False,
                authority_type=ClearanceAuthority.AUTONOMOUS,
                status_code="REJECTED_ROLLOUT_DISABLED",
                reason="ROLLOUT_DISABLED: Consumption of autonomous execution authority is disabled or kill-switch is engaged.",
                record_id=res_id,
                digest=res_digest,
            )

    # Scope checks: wave, run_id, worktree
    if req.wave is not None and int(wave) != int(req.wave):
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=ClearanceAuthority.AUTONOMOUS,
            status_code="REJECTED_SCOPE_MISMATCH",
            reason=f"SCOPE_MISMATCH: Autonomous record bound to Wave {wave}, verification requires Wave {req.wave}.",
            record_id=res_id,
            digest=res_digest,
        )

    if req.worktree and wt and str(wt) != str(req.worktree):
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=ClearanceAuthority.AUTONOMOUS,
            status_code="REJECTED_SCOPE_MISMATCH",
            reason=f"SCOPE_MISMATCH: Autonomous record bound to worktree '{wt}', verification requires '{req.worktree}'.",
            record_id=res_id,
            digest=res_digest,
        )

    # Policy freshness check
    if req.policy_digest and pol_digest and pol_digest != req.policy_digest:
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=ClearanceAuthority.AUTONOMOUS,
            status_code="REJECTED_POLICY_STALE",
            reason=f"STALE_POLICY: Autonomous record policy digest '{pol_digest[:16]}' differs from active '{req.policy_digest[:16]}'.",
            record_id=res_id,
            digest=res_digest,
        )

    return ClearanceVerificationResult(
        cleared=True,
        authority_type=ClearanceAuthority.AUTONOMOUS,
        status_code="CLEARED_AUTONOMOUS",
        reason=f"Verified autonomous resolution in store (seq: {seq_num}, signer: {signer_id})",
        record_id=res_id,
        digest=res_digest,
        selected_option_id=sel_opt_id,
    )


def _query_autonomous_sqlite_store(
    sqlite_path: Path,
    req: ClearanceVerificationRequest,
) -> Optional[ClearanceVerificationResult]:
    """Queries autonomous resolutions table in SQLite store with exact ID matching."""
    conn = create_sqlite_conn(sqlite_path, readonly=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autonomous_resolutions'"
        )
        if not cur.fetchone():
            return None

        cur.execute(
            """
            SELECT * FROM autonomous_resolutions
            WHERE decision_id = ?
            ORDER BY sequence_number DESC LIMIT 1
            """,
            (req.decision_id,),
        )
        row = cur.fetchone()
        if row:
            return _verify_autonomous_sqlite_row(row, req)
        return None
    finally:
        conn.close()


def _query_json_store_fallback(
    json_path: Path,
    req: ClearanceVerificationRequest,
) -> Optional[ClearanceVerificationResult]:
    if not json_path.is_file():
        return None
    try:
        records = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return None
        for raw in reversed(records):
            norm = normalize_legacy_record(raw)
            if not norm or norm["decision_id"] != req.decision_id:
                continue

            if not norm["is_origin_valid"]:
                return ClearanceVerificationResult(
                    cleared=False,
                    authority_type=ClearanceAuthority.HUMAN,
                    status_code="REJECTED_SYNTHETIC",
                    reason=f"SYNTHETIC_APPROVAL_REJECTED: {norm['origin_rejection_reason']}",
                    operator_id=norm["operator_id"],
                )

            sel = str(norm["selected_option"] or "").upper()
            if sel in ("DECLINED", "CANCELLED", "OPT_DECLINE") or norm["outcome_type"] == "OVERRIDDEN":
                return ClearanceVerificationResult(
                    cleared=False,
                    authority_type=ClearanceAuthority.HUMAN,
                    status_code="REJECTED_DECLINED",
                    reason="DECLINED: Decision explicitly declined in JSON ledger.",
                    operator_id=norm["operator_id"],
                )

            if req.wave is not None and norm["wave"] is not None and int(norm["wave"]) != int(req.wave):
                return ClearanceVerificationResult(
                    cleared=False,
                    authority_type=ClearanceAuthority.HUMAN,
                    status_code="REJECTED_SCOPE_MISMATCH",
                    reason=f"SCOPE_MISMATCH: JSON record wave {norm['wave']} != requested {req.wave}",
                    operator_id=norm["operator_id"],
                )

            return ClearanceVerificationResult(
                cleared=True,
                authority_type=ClearanceAuthority.HUMAN,
                status_code="CLEARED_HUMAN",
                reason=f"Verified in JSON ledger fallback for '{req.decision_id}'",
                digest=norm["entry_digest"],
                operator_id=norm["operator_id"],
            )
    except Exception as exc:
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=None,
            status_code="REJECTED_CORRUPT_STORE",
            reason=f"Error inspecting JSON ledger: {exc}",
        )
    return None


def verify_decision_clearance(
    req: ClearanceVerificationRequest,
    sqlite_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
) -> ClearanceVerificationResult:
    """Exact-scope read-only decision clearance verifier."""
    dec_id = req.decision_id.strip()
    if not dec_id:
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=None,
            status_code="INVALID_INPUT",
            reason="INVALID_INPUT: Empty or whitespace decision_id.",
        )

    # 1. Authoritative SQLite Store Check
    if sqlite_path and sqlite_path.is_file():
        # Query human SQLite table first to catch synthetic approvals
        human_res, table_exists = _query_human_sqlite_store(sqlite_path, req)
        if human_res and human_res.status_code == "REJECTED_SYNTHETIC":
            return human_res

        chain_ok, chain_msg = _check_sqlite_integrity_safely(sqlite_path)
        if not chain_ok:
            return ClearanceVerificationResult(
                cleared=False,
                authority_type=None,
                status_code="REJECTED_CORRUPT_STORE",
                reason=f"LEDGER_CHAIN_INTEGRITY_FAILURE: {chain_msg}",
            )

        if human_res:
            return human_res

        # Query autonomous SQLite table
        auto_res = _query_autonomous_sqlite_store(sqlite_path, req)
        if auto_res:
            return auto_res

        # SQLite exists and has no match: Do NOT fall back to JSON
        return ClearanceVerificationResult(
            cleared=False,
            authority_type=None,
            status_code="UNRATIFIED",
            reason=f"Decision '{dec_id}' not found in authoritative SQLite store.",
        )

    # 2. JSON store fallback ONLY if SQLite does not exist
    if json_path and json_path.is_file():
        json_res = _query_json_store_fallback(json_path, req)
        if json_res:
            return json_res

    return ClearanceVerificationResult(
        cleared=False,
        authority_type=None,
        status_code="UNRATIFIED",
        reason=f"Decision '{dec_id}' not found in SQLite or JSON ledger store.",
    )


def is_decision_ratified_in_ledger(
    decision_id: str,
    sqlite_path: Path,
    json_path: Path,
) -> Tuple[bool, str]:
    """Compatibility adapter for legacy is_decision_ratified_in_ledger callers."""
    req = ClearanceVerificationRequest(decision_id=decision_id)
    res = verify_decision_clearance(req, sqlite_path=sqlite_path, json_path=json_path)
    return res.cleared, res.reason


def verify_decision_ratification(
    decision: PreWaveDecision,
    ledger_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Compatibility adapter for pre-wave decision interceptor ratification checking."""
    from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths

    sqlite_target = None
    if ledger_path:
        if str(ledger_path).endswith(".sqlite"):
            sqlite_target = Path(ledger_path)
        elif Path(ledger_path).with_suffix(".sqlite").is_file():
            sqlite_target = Path(ledger_path).with_suffix(".sqlite")

    s_path, j_path = resolve_ledger_paths(
        sqlite_path=sqlite_target,
        json_path=ledger_path if ledger_path and str(ledger_path).endswith(".json") else None,
    )
    req = ClearanceVerificationRequest(
        decision_id=decision.decision_id,
        wave=decision.wave,
        options=decision.options,
    )
    res = verify_decision_clearance(req, sqlite_path=s_path, json_path=j_path)
    return res.cleared, res.reason
