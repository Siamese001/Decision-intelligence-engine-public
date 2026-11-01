"""
dual_pipeline/hitl_governance_core/autonomous_resolution/storage.py

Layer-0 append-only SQLite storage engine for authenticated autonomous resolutions.
Enforces multi-process serialization, strict sequence monotonic progression,
cryptographic Merkle chaining, and semantic idempotency.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from storage.connection import create_sqlite_conn

from .models import (
    AuthorityType,
    AutonomousResolutionRecord,
    DecisionOption,
    DecisionRequest,
    DecisionScope,
    EligibilityEvaluationResult,
    PolicySnapshot,
)
from .signing import (
    compute_canonical_digest,
    sign_autonomous_payload,
    verify_autonomous_signature,
)

AUTONOMOUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomous_resolutions (
    resolution_id TEXT PRIMARY KEY,
    sequence_number INTEGER NOT NULL UNIQUE,
    decision_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    worktree TEXT NOT NULL,
    run_id TEXT NOT NULL,
    wave INTEGER NOT NULL,
    action_target TEXT NOT NULL,
    authority_type TEXT NOT NULL,
    category TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    selected_option_id TEXT NOT NULL,
    selected_option_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    margin REAL NOT NULL,
    entropy REAL NOT NULL,
    top_confidence REAL NOT NULL,
    second_confidence REAL NOT NULL,
    policy_digest TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL,
    decision_content_digest TEXT NOT NULL,
    previous_resolution_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    signer_id TEXT NOT NULL,
    signature_b64 TEXT NOT NULL,
    resolution_digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_decision_id ON autonomous_resolutions(decision_id);
CREATE INDEX IF NOT EXISTS idx_auto_idempotency ON autonomous_resolutions(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_auto_sequence ON autonomous_resolutions(sequence_number);
"""


class ConflictResolutionError(Exception):
    """Raised when an idempotency key matches an existing record with different payload."""


def compute_idempotency_key(
    decision_id: str,
    revision: int,
    wave: int,
    action_target: str,
) -> str:
    """Computes a stable semantic idempotency key independent of transport request IDs."""
    prefix = decision_id.strip()
    target_clean = action_target.strip().replace("/", "_")
    return f"auto_idemp_{prefix}_r{revision}_w{wave}_{target_clean}"


def _row_to_record(row: Tuple[Any, ...]) -> AutonomousResolutionRecord:
    """Converts SQLite tuple row into typed AutonomousResolutionRecord."""
    (
        res_id,
        seq_num,
        dec_id,
        rev,
        wt,
        run_id,
        wave,
        target,
        auth_type_raw,
        cat,
        idemp_key,
        sel_opt_id,
        sel_opt_json,
        opts_json,
        margin,
        entropy,
        top_conf,
        sec_conf,
        _pol_digest,
        pol_snap_json,
        dec_content_digest,
        prev_hash,
        rec_at,
        signer_id,
        sig_b64,
        res_digest,
    ) = row

    sel_opt = DecisionOption.from_dict(json.loads(sel_opt_json))
    opts = [DecisionOption.from_dict(o) for o in json.loads(opts_json)]
    pol_snap = PolicySnapshot.from_dict(json.loads(pol_snap_json))

    return AutonomousResolutionRecord(
        resolution_id=res_id,
        sequence_number=seq_num,
        decision_id=dec_id,
        revision=rev,
        scope=DecisionScope(worktree=wt, run_id=run_id, wave=wave, action_target=target),
        authority_type=AuthorityType(auth_type_raw),
        category=cat,
        idempotency_key=idemp_key,
        selected_option_id=sel_opt_id,
        selected_option=sel_opt,
        options=opts,
        margin=float(margin),
        entropy=float(entropy),
        top_confidence=float(top_conf),
        second_confidence=float(sec_conf),
        policy_snapshot=pol_snap,
        decision_content_digest=dec_content_digest,
        previous_resolution_hash=prev_hash,
        recorded_at=rec_at,
        signer_id=signer_id,
        signature_b64=sig_b64,
        resolution_digest=res_digest,
    )


class AutonomousResolutionStore:
    """Append-only, concurrency-safe SQLite store for autonomous decision resolutions."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> Any:
        return create_sqlite_conn(self.db_path, readonly=False)

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            conn.executescript(AUTONOMOUS_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def record_resolution(
        self,
        request: DecisionRequest,
        eval_result: EligibilityEvaluationResult,
        private_key: Ed25519PrivateKey,
        signer_id: str = "engine_autonomous_authority_01",
    ) -> AutonomousResolutionRecord:
        """
        Appends an authenticated, signed autonomous resolution record under transactional serialization.
        Replays existing record idempotently if identical; rejects conflicts.
        """
        if not eval_result.is_eligible or eval_result.selected_option is None:
            raise ValueError("Cannot persist ineligible or unresolved decision evaluation.")

        content_dict = {
            "decision_id": request.decision_id,
            "revision": request.revision,
            "scope": request.scope.to_dict(),
            "options": [o.to_dict() for o in request.options],
        }
        content_digest = compute_canonical_digest(content_dict)
        idemp_key = compute_idempotency_key(
            request.decision_id, request.revision, request.scope.wave, request.scope.action_target
        )

        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()

            # 1. Idempotency Check
            cur.execute("SELECT * FROM autonomous_resolutions WHERE idempotency_key = ?", (idemp_key,))
            existing_row = cur.fetchone()
            if existing_row:
                existing_rec = _row_to_record(existing_row)
                if (
                    existing_rec.decision_content_digest == content_digest
                    and existing_rec.selected_option_id == eval_result.selected_option_id
                ):
                    conn.commit()
                    return existing_rec
                raise ConflictResolutionError(
                    f"Idempotency conflict for key '{idemp_key}': payload or selected option differs."
                )

            # 2. Sequence and Merkle head query
            cur.execute(
                "SELECT sequence_number, resolution_digest FROM autonomous_resolutions "
                "ORDER BY sequence_number DESC LIMIT 1"
            )
            head_row = cur.fetchone()
            if head_row:
                next_seq = head_row[0] + 1
                prev_hash = head_row[1]
            else:
                next_seq = 1
                prev_hash = "0" * 64

            now_iso = datetime.now(timezone.utc).isoformat()
            res_id = f"auto_res_{uuid.uuid4().hex[:16]}"

            canonical_payload = {
                "decision_content_digest": content_digest,
                "decision_id": request.decision_id,
                "entropy": round(float(eval_result.entropy), 4),
                "margin": round(float(eval_result.margin), 4),
                "policy_digest": eval_result.policy_snapshot.policy_digest,
                "previous_resolution_hash": prev_hash,
                "recorded_at": now_iso,
                "revision": request.revision,
                "scope": request.scope.to_dict(),
                "second_confidence": round(float(eval_result.second_confidence), 4),
                "selected_option_id": eval_result.selected_option_id,
                "sequence_number": next_seq,
                "signer_id": signer_id,
                "top_confidence": round(float(eval_result.top_confidence), 4),
            }

            sig_b64 = sign_autonomous_payload(private_key, canonical_payload)
            res_digest = compute_canonical_digest(canonical_payload)

            cur.execute(
                """
                INSERT INTO autonomous_resolutions (
                    resolution_id, sequence_number, decision_id, revision,
                    worktree, run_id, wave, action_target, authority_type, category,
                    idempotency_key, selected_option_id, selected_option_json, options_json,
                    margin, entropy, top_confidence, second_confidence,
                    policy_digest, policy_snapshot_json, decision_content_digest,
                    previous_resolution_hash, recorded_at, signer_id, signature_b64, resolution_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    res_id,
                    next_seq,
                    request.decision_id,
                    request.revision,
                    request.scope.worktree,
                    request.scope.run_id,
                    request.scope.wave,
                    request.scope.action_target,
                    request.authority_type.value,
                    request.category,
                    idemp_key,
                    eval_result.selected_option_id,
                    json.dumps(eval_result.selected_option.to_dict(), sort_keys=True),
                    json.dumps([o.to_dict() for o in request.options], sort_keys=True),
                    eval_result.margin,
                    eval_result.entropy,
                    eval_result.top_confidence,
                    eval_result.second_confidence,
                    eval_result.policy_snapshot.policy_digest,
                    json.dumps(eval_result.policy_snapshot.to_dict(), sort_keys=True),
                    content_digest,
                    prev_hash,
                    now_iso,
                    signer_id,
                    sig_b64,
                    res_digest,
                ),
            )
            conn.commit()

            return AutonomousResolutionRecord(
                resolution_id=res_id,
                sequence_number=next_seq,
                decision_id=request.decision_id,
                revision=request.revision,
                scope=request.scope,
                authority_type=request.authority_type,
                category=request.category,
                idempotency_key=idemp_key,
                selected_option_id=eval_result.selected_option_id,
                selected_option=eval_result.selected_option,
                options=request.options,
                margin=eval_result.margin,
                entropy=eval_result.entropy,
                top_confidence=eval_result.top_confidence,
                second_confidence=eval_result.second_confidence,
                policy_snapshot=eval_result.policy_snapshot,
                decision_content_digest=content_digest,
                previous_resolution_hash=prev_hash,
                recorded_at=now_iso,
                signer_id=signer_id,
                signature_b64=sig_b64,
                resolution_digest=res_digest,
            )
        finally:
            conn.close()

    def get_by_resolution_id(self, resolution_id: str) -> Optional[AutonomousResolutionRecord]:
        """Fetches resolution by its primary resolution ID."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM autonomous_resolutions WHERE resolution_id = ?", (resolution_id,))
            row = cur.fetchone()
            return _row_to_record(row) if row else None
        finally:
            conn.close()

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[AutonomousResolutionRecord]:
        """Fetches resolution by its semantic idempotency key."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM autonomous_resolutions WHERE idempotency_key = ?", (idempotency_key,))
            row = cur.fetchone()
            return _row_to_record(row) if row else None
        finally:
            conn.close()

    def list_all(self) -> List[AutonomousResolutionRecord]:
        """Lists all resolutions ordered by sequence_number ascending."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM autonomous_resolutions ORDER BY sequence_number ASC")
            return [_row_to_record(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def verify_integrity(self, public_key: Ed25519PublicKey) -> Tuple[bool, List[str]]:
        """Verifies sequence continuity, Merkle hashes, and Ed25519 signatures across all records."""
        records = self.list_all()
        defects: List[str] = []
        expected_prev_hash = "0" * 64

        for idx, rec in enumerate(records):
            expected_seq = idx + 1
            if rec.sequence_number != expected_seq:
                defects.append(
                    f"Sequence broken at record {rec.resolution_id}: got {rec.sequence_number}, expected {expected_seq}."
                )
            if rec.previous_resolution_hash != expected_prev_hash:
                defects.append(
                    f"Merkle chain broken at record {rec.resolution_id}: previous_hash '{rec.previous_resolution_hash}' "
                    f"does not match expected '{expected_prev_hash}'."
                )

            signed_payload = rec.canonical_signed_payload()
            computed_digest = compute_canonical_digest(signed_payload)
            if rec.resolution_digest != computed_digest:
                defects.append(
                    f"Digest mismatch on record {rec.resolution_id}: record digest '{rec.resolution_digest}' "
                    f"differs from recomputed '{computed_digest}'."
                )

            is_valid_sig = verify_autonomous_signature(
                public_key=public_key,
                payload=signed_payload,
                signature_b64=rec.signature_b64,
            )
            if not is_valid_sig:
                defects.append(f"Invalid digital signature on record {rec.resolution_id}.")

            expected_prev_hash = rec.resolution_digest

        return len(defects) == 0, defects
