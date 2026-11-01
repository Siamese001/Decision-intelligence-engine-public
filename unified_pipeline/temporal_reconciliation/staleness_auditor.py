"""Temporal decay & staleness auditor for active knowledge graph edges.

Scans active graph edges for mutable role predicates and flags relationships
exceeding configured time-to-live (TTL) thresholds without re-attestation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from storage.connection import create_sqlite_conn

MUTABLE_ROLE_PREDICATES: tuple[str, ...] = (
    "OWNS_OR_LEADS",
    "DATED_ROLE_IN",
    "REPORTS_TO",
    "LED_BY",
    "CONTRIBUTES_TO",
    "DATED_CONTRIBUTOR_TO",
    "DATED_CHARTER_ROLE_CUE_FOR",
    "ROSTER_ROW_IN",
    "AFFILIATED_WITH",
    "OWNS",
    "SPONSORS",
    "OWNS_OR_COORDINATES",
)


@dataclass(frozen=True)
class StalenessRecord:
    """Individual stale edge observation."""

    edge_id: str
    from_node_id: str
    from_node_name: str
    edge_type: str
    to_node_id: str
    to_node_name: str
    source_id: str
    source_name: str
    source_date: str
    age_days: int
    staleness_tier: str
    recommended_action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StalenessAuditReport:
    """Structured report produced by a staleness audit pass."""

    reference_date: str
    db_path: str
    warning_ttl_days: int
    critical_ttl_days: int
    total_active_edges_scanned: int
    mutable_edges_checked: int
    stale_count_p1: int
    stale_count_p2: int
    records: List[StalenessRecord]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_date": self.reference_date,
            "db_path": self.db_path,
            "warning_ttl_days": self.warning_ttl_days,
            "critical_ttl_days": self.critical_ttl_days,
            "total_active_edges_scanned": self.total_active_edges_scanned,
            "mutable_edges_checked": self.mutable_edges_checked,
            "stale_count_p1": self.stale_count_p1,
            "stale_count_p2": self.stale_count_p2,
            "records": [r.to_dict() for r in self.records],
        }


class StalenessAuditor:
    """Audits persistent SQLite graph stores for temporal staleness and unverified tenure."""

    def __init__(
        self,
        db_path: Path | str,
        warning_ttl_days: int = 45,
        critical_ttl_days: int = 90,
        mutable_predicates: Optional[Sequence[str]] = None,
    ) -> None:
        raw_p = Path(db_path) if isinstance(db_path, str) else db_path
        self.db_path = raw_p.resolve()
        self.warning_ttl = warning_ttl_days
        self.critical_ttl = critical_ttl_days
        self.predicates = set(mutable_predicates or MUTABLE_ROLE_PREDICATES)

    def _parse_source_date(self, raw_val: Optional[str]) -> Optional[date]:
        if not raw_val:
            return None
        clean = raw_val.strip()[:10]
        try:
            return datetime.strptime(clean, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _classify_tier(self, age_days: int) -> tuple[str, str]:
        if age_days >= self.critical_ttl:
            return "P1", "RETIRE_OR_REPLACE"
        if age_days >= self.warning_ttl:
            return "P2", "VERIFY_CURRENTNESS"
        return "FRESH", "NONE"

    def audit(self, reference_date: Optional[str | date] = None) -> StalenessAuditReport:
        ref_dt = self._resolve_reference_date(reference_date)
        conn = create_sqlite_conn(self.db_path, readonly=True)
        try:
            return self._scan_edges(conn, ref_dt)
        finally:
            conn.close()

    def _resolve_reference_date(self, ref: Optional[str | date]) -> date:
        if isinstance(ref, date):
            return ref
        if isinstance(ref, str):
            return datetime.strptime(ref.strip()[:10], "%Y-%m-%d").date()
        return date.today()

    def _resolve_node_name_expr(self, conn: Any, table_alias: str, fallback_col: str) -> str:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(node);")
        cols = {row[1] for row in cur.fetchall()}
        candidates: list[str] = []
        for col in ("display_name", "canonical_name", "name", "label"):
            if col in cols:
                candidates.append(f"{table_alias}.{col}")
        candidates.append(fallback_col)
        return f"COALESCE({', '.join(candidates)})"

    def _scan_edges(self, conn: Any, ref_dt: date) -> StalenessAuditReport:
        from_expr = self._resolve_node_name_expr(conn, "fn", "e.from_node_id")
        to_expr = self._resolve_node_name_expr(conn, "tn", "e.to_node_id")
        query = f"""
            SELECT
                e.edge_id,
                e.from_node_id,
                {from_expr} AS from_name,
                e.edge_type,
                e.to_node_id,
                {to_expr} AS to_name,
                e.source_id,
                COALESCE(s.source_name, e.source_id) AS source_name,
                COALESCE(s.source_date, substr(e.updated_at, 1, 10), substr(e.created_at, 1, 10)) AS eff_date
            FROM edge e
            LEFT JOIN node fn ON e.from_node_id = fn.node_id
            LEFT JOIN node tn ON e.to_node_id = tn.node_id
            LEFT JOIN source s ON e.source_id = s.source_id
            WHERE e.superseded_by IS NULL
        """
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()

        records: List[StalenessRecord] = []
        total_scanned = len(rows)
        checked_mutable = 0
        p1_count = 0
        p2_count = 0

        for row in rows:
            edge_type = row["edge_type"]
            if edge_type not in self.predicates:
                continue
            checked_mutable += 1

            parsed_dt = self._parse_source_date(row["eff_date"])
            if not parsed_dt:
                continue

            age_days = (ref_dt - parsed_dt).days
            tier, action = self._classify_tier(age_days)
            if tier == "FRESH":
                continue

            if tier == "P1":
                p1_count += 1
            elif tier == "P2":
                p2_count += 1

            records.append(
                StalenessRecord(
                    edge_id=row["edge_id"],
                    from_node_id=row["from_node_id"],
                    from_node_name=str(row["from_name"]),
                    edge_type=edge_type,
                    to_node_id=row["to_node_id"],
                    to_node_name=str(row["to_name"]),
                    source_id=row["source_id"],
                    source_name=str(row["source_name"]),
                    source_date=parsed_dt.isoformat(),
                    age_days=age_days,
                    staleness_tier=tier,
                    recommended_action=action,
                )
            )

        records.sort(key=lambda r: r.age_days, reverse=True)
        return StalenessAuditReport(
            reference_date=ref_dt.isoformat(),
            db_path=str(self.db_path),
            warning_ttl_days=self.warning_ttl,
            critical_ttl_days=self.critical_ttl,
            total_active_edges_scanned=total_scanned,
            mutable_edges_checked=checked_mutable,
            stale_count_p1=p1_count,
            stale_count_p2=p2_count,
            records=records,
        )


def export_staleness_report(report: StalenessAuditReport, target_path: Path | str) -> Path:
    out = Path(target_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return out
