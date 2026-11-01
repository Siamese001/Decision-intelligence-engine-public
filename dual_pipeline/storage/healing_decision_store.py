"""
dual_pipeline/storage/healing_decision_store.py

Governed Layer-0 SQLite persistent store for all healing and repair decisions
across UnifiedL2HealerService, DeterministicHealer mesh, MicroHealingDispatcher,
SurgicalRemediator, remediation_healer, document_connectivity_healer,
SelectiveRemediationStateMachine, and healing_policy.

Managed exclusively via GovernedStorageContext (WAL mode, 30,000ms busy timeout,
thread-safe WriteSerializer) for zero-contention persistence and offline system learning.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.core import digest, stable_id, utc_now
from dual_pipeline.storage.governed_context import GovernedStorageContext

logger = logging.getLogger(__name__)

DEFAULT_HEALING_SQLITE_PATH = Path(".local/ledger/learning.sqlite")

SUCCESS_OUTCOMES = frozenset({"HEALED", "AUTO_APPLY", "PASS", "REPAIRED", "COMPLETED"})


def get_default_healing_sqlite_path() -> Path:
    """Resolve canonical healing SQLite path, isolating test environments automatically."""
    from config.paths import get_learning_sqlite_path

    override = (
        CredentialManager.get_credential("NEE_TEST_LEARNING_SQLITE")
        or CredentialManager.get_credential("NEE_HEALING_SQLITE_PATH")
        or CredentialManager.get_credential("NEE_LEARNING_SQLITE_PATH")
    )
    if override:
        return Path(override).resolve()
    if (
        CredentialManager.get_credential("NEE_ALLOW_TEST_FIXTURES") == "1"
        or (CredentialManager.get_credential("MOCK_ENV") or "").lower() == "true"
        or CredentialManager.has_credential("PYTEST_CURRENT_TEST")
    ):
        return Path(tempfile.gettempdir()) / f"nee_test_learning_{os.getpid()}.sqlite"
    env_rt = CredentialManager.get_credential("NEE_RUNTIME_ROOT")
    if env_rt:
        return (Path(env_rt) / "ledger" / "learning.sqlite").resolve()
    return get_learning_sqlite_path()


HEALING_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS healing_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    subsystem TEXT NOT NULL,
    component TEXT NOT NULL,
    scope TEXT NOT NULL,
    repair_class TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    outcome_status TEXT NOT NULL,
    before_digest TEXT NOT NULL,
    after_digest TEXT NOT NULL,
    confidence_top REAL NOT NULL DEFAULT 1.0,
    confidence_second REAL NOT NULL DEFAULT 0.0,
    ambiguity_margin REAL NOT NULL DEFAULT 1.0,
    hitl_escalated INTEGER NOT NULL DEFAULT 0,
    invariants_preserved INTEGER NOT NULL DEFAULT 1,
    defect_reasons_json TEXT NOT NULL DEFAULT '[]',
    audit_trail_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    entry_digest TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_healing_subsystem ON healing_decisions(subsystem);
CREATE INDEX IF NOT EXISTS idx_healing_repair_class ON healing_decisions(repair_class);
CREATE INDEX IF NOT EXISTS idx_healing_outcome ON healing_decisions(outcome_status);
CREATE INDEX IF NOT EXISTS idx_healing_scope ON healing_decisions(scope);
CREATE INDEX IF NOT EXISTS idx_healing_created ON healing_decisions(created_at);
"""

INSERT_HEALING_DECISION_SQL = """
INSERT OR REPLACE INTO healing_decisions (
    decision_id, run_id, subsystem, component, scope,
    repair_class, action_taken, outcome_status,
    before_digest, after_digest, confidence_top, confidence_second,
    ambiguity_margin, hitl_escalated, invariants_preserved,
    defect_reasons_json, audit_trail_json, metadata_json,
    created_at, entry_digest
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class HealingDecisionRecord:
    decision_id: str
    run_id: str
    subsystem: str
    component: str
    scope: str
    repair_class: str
    action_taken: str
    outcome_status: str
    before_digest: str
    after_digest: str
    confidence_top: float = 0.80
    confidence_second: float = 0.0
    ambiguity_margin: float = 0.80
    hitl_escalated: bool = False
    invariants_preserved: bool = True
    defect_reasons: List[str] = field(default_factory=list)
    audit_trail: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    entry_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "subsystem": self.subsystem,
            "component": self.component,
            "scope": self.scope,
            "repair_class": self.repair_class,
            "action_taken": self.action_taken,
            "outcome_status": self.outcome_status,
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "confidence_top": round(float(self.confidence_top), 4),
            "confidence_second": round(float(self.confidence_second), 4),
            "ambiguity_margin": round(float(self.ambiguity_margin), 4),
            "hitl_escalated": bool(self.hitl_escalated),
            "invariants_preserved": bool(self.invariants_preserved),
            "defect_reasons": list(self.defect_reasons),
            "audit_trail": list(self.audit_trail),
            "metadata": dict(self.metadata),
            "created_at": self.created_at or utc_now(),
        }
        payload["entry_digest"] = self.entry_digest or digest(payload)
        return payload


def _str_or_default(d: Mapping[str, Any], key: str, default: str) -> str:
    val = d.get(key)
    return str(val) if val else default


def _digest_or_content(d: Mapping[str, Any], dig_key: str, content_key: str) -> str:
    val = d.get(dig_key)
    return str(val) if val else digest(str(d.get(content_key, "")))


def _normalize_record(record: Union[HealingDecisionRecord, Mapping[str, Any]]) -> HealingDecisionRecord:
    d = record.to_dict() if isinstance(record, HealingDecisionRecord) else dict(record)
    created_at = _str_or_default(d, "created_at", utc_now())
    subsystem = _str_or_default(d, "subsystem", "unknown_subsystem")
    component = _str_or_default(d, "component", subsystem)
    repair_class = _str_or_default(d, "repair_class", "GENERIC_FIELD_CLEANUP")
    before_dig = _digest_or_content(d, "before_digest", "before_content")
    after_dig = _digest_or_content(d, "after_digest", "after_content")
    run_id = _str_or_default(d, "run_id", "run-healing-default")
    raw_c_top = d.get("confidence_top")
    raw_c_sec = d.get("confidence_second")
    c_top = float(0.80 if raw_c_top is None else raw_c_top)
    c_sec = float(0.0 if raw_c_sec is None else raw_c_sec)
    raw_margin = d.get("ambiguity_margin")
    margin_val = float(max(0.0, c_top - c_sec) if raw_margin is None else raw_margin)
    default_id = stable_id("heal", run_id, subsystem, component, repair_class, before_dig[:12], after_dig[:12], created_at)
    temp = HealingDecisionRecord(
        decision_id=_str_or_default(d, "decision_id", default_id),
        run_id=run_id,
        subsystem=subsystem,
        component=component,
        scope=_str_or_default(d, "scope", "general"),
        repair_class=repair_class,
        action_taken=_str_or_default(d, "action_taken", "REPAIR_ATTEMPT"),
        outcome_status=_str_or_default(d, "outcome_status", "HEALED"),
        before_digest=before_dig,
        after_digest=after_dig,
        confidence_top=c_top,
        confidence_second=c_sec,
        ambiguity_margin=margin_val,
        hitl_escalated=bool(d.get("hitl_escalated", False)),
        invariants_preserved=bool(d.get("invariants_preserved", True)),
        defect_reasons=[str(x) for x in (d.get("defect_reasons") or [])],
        audit_trail=list(d.get("audit_trail") or []),
        metadata=dict(d.get("metadata") or {}),
        created_at=created_at,
    )
    return HealingDecisionRecord(**temp.to_dict())


def _record_to_sql_tuple(rec: HealingDecisionRecord) -> Tuple[Any, ...]:
    d = rec.to_dict()
    return (
        d["decision_id"],
        d["run_id"],
        d["subsystem"],
        d["component"],
        d["scope"],
        d["repair_class"],
        d["action_taken"],
        d["outcome_status"],
        d["before_digest"],
        d["after_digest"],
        float(d["confidence_top"]),
        float(d["confidence_second"]),
        float(d["ambiguity_margin"]),
        1 if d["hitl_escalated"] else 0,
        1 if d["invariants_preserved"] else 0,
        json.dumps(d["defect_reasons"], sort_keys=True),
        json.dumps(d["audit_trail"], sort_keys=True),
        json.dumps(d["metadata"], sort_keys=True),
        d["created_at"],
        d["entry_digest"],
    )


def _row_to_dict(row: Tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "decision_id": row[0],
        "run_id": row[1],
        "subsystem": row[2],
        "component": row[3],
        "scope": row[4],
        "repair_class": row[5],
        "action_taken": row[6],
        "outcome_status": row[7],
        "before_digest": row[8],
        "after_digest": row[9],
        "confidence_top": float(row[10]),
        "confidence_second": float(row[11]),
        "ambiguity_margin": float(row[12]),
        "hitl_escalated": bool(row[13]),
        "invariants_preserved": bool(row[14]),
        "defect_reasons": json.loads(row[15]) if row[15] else [],
        "audit_trail": json.loads(row[16]) if row[16] else [],
        "metadata": json.loads(row[17]) if row[17] else {},
        "created_at": row[18],
        "entry_digest": row[19],
    }


class HealingDecisionStore:
    """Governed SQLite persistent store for healing decisions and empirical repair posteriors."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        self.db_path = Path(db_path).resolve() if db_path is not None else get_default_healing_sqlite_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            conn.executescript(HEALING_DECISIONS_SCHEMA)

    def record_decision(
        self, record: Union[HealingDecisionRecord, Mapping[str, Any]]
    ) -> HealingDecisionRecord:
        norm = _normalize_record(record)
        row = _record_to_sql_tuple(norm)
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            conn.execute(INSERT_HEALING_DECISION_SQL, row)
        return norm

    def record_decisions_batch(
        self, records: Sequence[Union[HealingDecisionRecord, Mapping[str, Any]]]
    ) -> List[HealingDecisionRecord]:
        normalized = [_normalize_record(r) for r in records]
        if not normalized:
            return []
        rows = [_record_to_sql_tuple(n) for n in normalized]
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            conn.executemany(INSERT_HEALING_DECISION_SQL, rows)
        return normalized

    def list_decisions(
        self,
        subsystem: Optional[str] = None,
        outcome_status: Optional[str] = None,
        scope: Optional[str] = None,
        repair_class: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if subsystem:
            clauses.append("subsystem = ?")
            params.append(subsystem)
        if outcome_status:
            clauses.append("outcome_status = ?")
            params.append(outcome_status)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if repair_class:
            clauses.append("repair_class = ?")
            params.append(repair_class)
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT decision_id, run_id, subsystem, component, scope, repair_class, "
            "action_taken, outcome_status, before_digest, after_digest, confidence_top, "
            "confidence_second, ambiguity_margin, hitl_escalated, invariants_preserved, "
            "defect_reasons_json, audit_trail_json, metadata_json, created_at, entry_digest "
            f"FROM healing_decisions {where_sql} ORDER BY created_at ASC, decision_id ASC LIMIT ?"
        )
        params.append(int(limit))
        with GovernedStorageContext.read_context(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update_post_learning_confidence(
        self,
        decision_id: str,
        calibrated_confidence: float,
        calibrated_margin: Optional[float] = None,
    ) -> bool:
        """Update post-learning calibrated confidence and margin for an existing healing decision."""
        new_top = round(max(0.0, min(1.0, float(calibrated_confidence))), 4)
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            row = conn.execute(
                "SELECT confidence_second, ambiguity_margin, metadata_json FROM healing_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if not row:
                return False
            c_sec = float(row[0] or 0.0)
            new_margin = round(float(calibrated_margin) if calibrated_margin is not None else max(0.0, new_top - c_sec), 4)
            meta = json.loads(row[2]) if row[2] else {}
            meta["post_learning_recalibrated"] = True
            meta["post_learning_confidence"] = new_top
            conn.execute(
                "UPDATE healing_decisions SET confidence_top = ?, ambiguity_margin = ?, metadata_json = ? WHERE decision_id = ?",
                (new_top, new_margin, json.dumps(meta, sort_keys=True), decision_id),
            )
        return True

    def compute_empirical_posteriors(
        self, prior_alpha: float = 2.0, prior_beta: float = 1.0
    ) -> Dict[str, Dict[str, Any]]:
        """Compute Laplace/Beta-Binomial empirical repair posteriors grouped by key."""
        sql = (
            "SELECT decision_id, run_id, subsystem, component, scope, repair_class, "
            "action_taken, outcome_status, before_digest, after_digest, confidence_top, "
            "confidence_second, ambiguity_margin, hitl_escalated, invariants_preserved, "
            "defect_reasons_json, audit_trail_json, metadata_json, created_at, entry_digest "
            "FROM healing_decisions ORDER BY created_at DESC, decision_id DESC LIMIT 10000"
        )
        with GovernedStorageContext.read_context(self.db_path) as conn:
            decisions = [_row_to_dict(r) for r in conn.execute(sql).fetchall()]
        buckets: Dict[str, Dict[str, int]] = {}
        for d in decisions:
            is_success = (
                str(d["outcome_status"]).upper() in SUCCESS_OUTCOMES
                and bool(d["invariants_preserved"])
                and not bool(d["hitl_escalated"])
            )
            keys = (
                f"{d['subsystem']}:{d['repair_class']}:{d['scope']}",
                f"repair_class:{d['repair_class']}",
                f"scope:{d['scope']}",
                f"subsystem:{d['subsystem']}",
            )
            for k in keys:
                b = buckets.setdefault(
                    k, {"total": 0, "success": 0, "failed": 0, "hitl_escalated": 0}
                )
                b["total"] += 1
                if is_success:
                    b["success"] += 1
                else:
                    b["failed"] += 1
                if d["hitl_escalated"]:
                    b["hitl_escalated"] += 1

        posteriors: Dict[str, Dict[str, Any]] = {}
        for k, stats in buckets.items():
            total = stats["total"]
            succ = stats["success"]
            post = (succ + prior_alpha) / (total + prior_alpha + prior_beta)
            posteriors[k] = {
                "total_count": total,
                "success_count": succ,
                "failure_count": stats["failed"],
                "hitl_escalated_count": stats["hitl_escalated"],
                "empirical_rate": round(succ / total, 4) if total > 0 else 0.0,
                "posterior_confidence": round(post, 4),
            }
        return posteriors


def record_healing_decision(
    record: Union[HealingDecisionRecord, Mapping[str, Any]],
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[HealingDecisionRecord]:
    """Fail-safe helper to persist a healing decision from any in-flight healer."""
    try:
        store = HealingDecisionStore(db_path=db_path)
        return store.record_decision(record)
    except Exception as exc:
        logger.warning("Non-fatal: failed to record healing decision to SQLite: %s", exc)
        return None
