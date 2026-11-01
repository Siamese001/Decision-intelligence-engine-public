"""
dual_pipeline/clearance_lifecycle_core/c10_register.py

Rule C-10: Append-Only Ambiguity Lock Register.
Maintains a tamper-evident, hash-chained log of all detected ambiguities
and enforces terminal clearance before Stage U0 admission.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from dual_pipeline.ambiguity_core.models import AmbiguityRecord

from .models import C10LockEntry

logger = logging.getLogger(__name__)


class AmbiguityLockRegister:
    """Rule C-10 Append-Only Ambiguity Lock Register with SHA-256 hash chaining."""

    def __init__(self) -> None:
        self._entries: List[C10LockEntry] = []
        self._locks_by_id: Dict[str, C10LockEntry] = {}

    @property
    def entries(self) -> List[C10LockEntry]:
        return list(self._entries)

    @property
    def latest_hash(self) -> str:
        return self._entries[-1].entry_hash if self._entries else "0" * 64

    def _compute_hash(self, lock_id: str, ambiguity_id: str, status: str, prev_hash: str) -> str:
        content = f"{lock_id}:{ambiguity_id}:{status}:{prev_hash}".encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def append_lock(self, record: AmbiguityRecord) -> C10LockEntry:
        """Appends a new lock entry from an AmbiguityRecord."""
        lock_id = f"c10_lock_{len(self._entries):05d}"
        prev = self.latest_hash
        cand_summary = " | ".join(f"{c.text} ({c.confidence})" for c in record.candidates[:3])
        entry_hash = self._compute_hash(lock_id, record.ambiguity_id, "OPEN", prev)

        entry = C10LockEntry(
            lock_id=lock_id,
            ambiguity_id=record.ambiguity_id,
            domain=record.domain.value,
            segment_id=record.segment_id,
            candidate_summary=cand_summary,
            requires_hitl=record.requires_hitl,
            status="OPEN",
            prev_hash=prev,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        self._locks_by_id[lock_id] = entry
        logger.debug(f"Rule C-10 lock appended: {lock_id} for {record.ambiguity_id} (requires_hitl={record.requires_hitl})")
        return entry

    def clear_lock(
        self,
        lock_id: str,
        resolution_payload: Dict[str, Any],
        status: str = "CLEARED",
    ) -> Optional[C10LockEntry]:
        """Marks a lock as cleared with an immutable resolution payload."""
        if lock_id not in self._locks_by_id:
            logger.warning(f"C-10 lock {lock_id} not found.")
            return None

        orig = self._locks_by_id[lock_id]
        prev = self.latest_hash
        new_hash = self._compute_hash(lock_id, orig.ambiguity_id, status, prev)

        cleared_entry = C10LockEntry(
            lock_id=lock_id,
            ambiguity_id=orig.ambiguity_id,
            domain=orig.domain,
            segment_id=orig.segment_id,
            candidate_summary=orig.candidate_summary,
            requires_hitl=orig.requires_hitl,
            status=status,
            resolution_payload=resolution_payload,
            prev_hash=prev,
            entry_hash=new_hash,
        )
        self._entries.append(cleared_entry)
        self._locks_by_id[lock_id] = cleared_entry
        logger.info(f"Rule C-10 lock cleared: {lock_id} -> {status}")
        return cleared_entry

    def is_cleared(self) -> bool:
        """Verifies whether all blocking (requires_hitl=True) locks are cleared."""
        for lock in self._locks_by_id.values():
            if lock.requires_hitl and lock.status == "OPEN":
                return False
        return True

    def get_open_hitl_locks(self) -> List[C10LockEntry]:
        """Returns all open locks that require HITL intervention."""
        return [l for l in self._locks_by_id.values() if l.requires_hitl and l.status == "OPEN"]

    def has_open_locks(self) -> bool:
        """Returns True if any blocking HITL locks remain OPEN."""
        return not self.is_cleared()
