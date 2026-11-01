"""
Environmental State Delta Auditor.

Measures verifiable external side-effects in SQLite databases and filesystem files,
certifying idempotency, positive mutations, and granular table transitions.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import sqlite3

GOVERNED_TABLES = (
    "node",
    "source",
    "source_version",
    "extraction_run",
    "evidence_item",
    "assertion",
    "assertion_evidence",
    "audit_event",
)


@dataclass
class DatabaseSnapshot:
    """Point-in-time snapshot of database state and table counts."""

    file_sha256: str
    table_counts: Dict[str, int]
    timestamp_iso: str


@dataclass
class EnvironmentalDeltaReport:
    """Comprehensive environmental state delta report."""

    db_path: str
    baseline_sha256: str
    activated_sha256: str
    hash_changed: bool
    is_idempotent_noop: bool
    table_deltas: Dict[str, int]
    total_mutations: int
    baseline_counts: Dict[str, int]
    activated_counts: Dict[str, int]
    itemized_table_breakdown: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StateDeltaAuditor:
    """Audits environmental state deltas before and after workflow executions."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()

    def capture_snapshot(self) -> DatabaseSnapshot:
        """Capture current database SHA256 and row counts across governed tables."""
        from datetime import datetime, timezone

        if not self.db_path.exists():
            return DatabaseSnapshot(
                file_sha256="0" * 64,
                table_counts={t: 0 for t in GOVERNED_TABLES},
                timestamp_iso=datetime.now(timezone.utc).isoformat(),
            )

        # Compute SHA256
        h = hashlib.sha256()
        with open(self.db_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        file_sha256 = h.hexdigest()

        # Query table counts
        counts: Dict[str, int] = {}
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        conn = GovernedStorageContext.connect(self.db_path, readonly=True)
        try:
            cur = conn.cursor()
            for tbl in GOVERNED_TABLES:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    row = cur.fetchone()
                    counts[tbl] = row[0] if row else 0
                except sqlite3.OperationalError:
                    counts[tbl] = 0
        finally:
            conn.close()

        return DatabaseSnapshot(
            file_sha256=file_sha256,
            table_counts=counts,
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )

    def compute_delta(
        self, baseline: DatabaseSnapshot, activated: Optional[DatabaseSnapshot] = None
    ) -> EnvironmentalDeltaReport:
        """Compute delta report between baseline snapshot and final snapshot."""
        if activated is None:
            activated = self.capture_snapshot()

        table_deltas: Dict[str, int] = {}
        total_mutations = 0
        itemized = []

        for tbl in GOVERNED_TABLES:
            b_cnt = baseline.table_counts.get(tbl, 0)
            a_cnt = activated.table_counts.get(tbl, 0)
            delta = a_cnt - b_cnt
            table_deltas[tbl] = delta
            total_mutations += abs(delta)

            itemized.append({
                "table": tbl,
                "baseline_count": b_cnt,
                "activated_count": a_cnt,
                "delta": f"+{delta}" if delta > 0 else str(delta),
                "what_changed": (
                    f"Added {delta} row(s)" if delta > 0
                    else (f"Removed {abs(delta)} row(s)" if delta < 0 else "Unchanged (+0)")
                ),
            })

        hash_changed = baseline.file_sha256 != activated.file_sha256
        is_idempotent_noop = (total_mutations == 0) and not hash_changed

        return EnvironmentalDeltaReport(
            db_path=str(self.db_path),
            baseline_sha256=baseline.file_sha256,
            activated_sha256=activated.file_sha256,
            hash_changed=hash_changed,
            is_idempotent_noop=is_idempotent_noop,
            table_deltas=table_deltas,
            total_mutations=total_mutations,
            baseline_counts=baseline.table_counts,
            activated_counts=activated.table_counts,
            itemized_table_breakdown=itemized,
        )
