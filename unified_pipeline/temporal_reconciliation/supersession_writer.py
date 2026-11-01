"""Governed temporal supersession writer with 5-check physical proof.

Executes atomic temporal supersession of retired or replaced roles, updating
superseded_by and valid_to while physically proving write durability on disk.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.graph_mutation_proof import GraphMutationProofError
from dual_pipeline.storage.governed_context import GLOBAL_WRITE_SERIALIZER
from storage.connection import create_sqlite_conn
from tools.graph_mutation_proof_core.proof import prove, snapshot


@dataclass(frozen=True)
class SupersessionRequest:
    """Ratified request to retire or replace an active graph edge."""

    target_edge_id: str
    effective_date: str
    reason: str
    approval_ref: str
    replacement_edge_id: Optional[str] = None
    resolution_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SupersessionResult:
    """Execution receipt documenting atomic supersession and physical proof."""

    target_edge_id: str
    superseded_by: str
    effective_date: str
    updated_at: str
    physical_proof: Dict[str, Any]
    status: str = "COMMITTED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TemporalSupersessionWriter:
    """Applies governed temporal updates with fail-closed physical proof."""

    def __init__(self, db_path: Path | str) -> None:
        raw_p = Path(db_path) if isinstance(db_path, str) else db_path
        self.db_path = raw_p.resolve()

    def apply_supersession(self, request: SupersessionRequest) -> SupersessionResult:
        with GLOBAL_WRITE_SERIALIZER.get_lock(str(self.db_path)):
            before = snapshot(self.db_path)
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            superseded_by_val = (
                request.replacement_edge_id
                or f"edge_retired_{uuid.uuid4().hex[:12]}"
            )

            conn = create_sqlite_conn(self.db_path, readonly=False)
            try:
                self._execute_db_update(conn, request, superseded_by_val, now_iso)
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            finally:
                conn.close()

            after = snapshot(self.db_path)
            proof_receipt = self._verify_physical_proof(
                before=before,
                after=after,
                target_edge_id=request.target_edge_id,
                superseded_by_val=superseded_by_val,
            )

            return SupersessionResult(
                target_edge_id=request.target_edge_id,
                superseded_by=superseded_by_val,
                effective_date=request.effective_date,
                updated_at=now_iso,
                physical_proof=proof_receipt,
            )

    def _execute_db_update(
        self,
        conn: Any,
        request: SupersessionRequest,
        superseded_by_val: str,
        now_iso: str,
    ) -> None:
        from dual_pipeline.graph_writer_core.supersession import apply_edge_supersession

        res_note = (
            request.resolution_note
            or f"Superseded by {superseded_by_val}: {request.reason}"
        )
        apply_edge_supersession(
            conn=conn,
            target_edge_id=request.target_edge_id,
            superseded_by_val=superseded_by_val,
            now_iso=now_iso,
            resolution_note=res_note,
        )

    def _verify_physical_proof(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        target_edge_id: str,
        superseded_by_val: str,
    ) -> Dict[str, Any]:
        probes = {
            "edge_superseded_probe": (
                "SELECT 1 FROM edge WHERE edge_id = ? AND superseded_by = ?",
                (target_edge_id, superseded_by_val),
            ),
        }
        proof = prove(
            before=before,
            after=after,
            expected_ids=[superseded_by_val],
            expected_deltas={},
            readback_probes=probes,
            strict_delta=False,
        )
        if proof["overall"] != "PASS":
            failed = [c["check"] for c in proof["checks"] if c["status"] != "PASS"]
            raise GraphMutationProofError(
                f"Physical proof failed on checks: {failed}",
                receipt=proof,
            )
        return proof
