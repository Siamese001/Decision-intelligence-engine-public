"""
dual_pipeline.graph_maintenance_core.harm_prioritized_planner

Harm-Prioritized Graph Maintenance Planner.
Joins hygiene findings, freshness history, and observed query impact metrics into
deduplicated, harm-ranked learning change proposals. Strictly read-only during planning,
enforcing approval gates and rollback controls on all proposed mutations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import stable_id
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
)

logger = logging.getLogger(__name__)

SEVERITY_WEIGHTS: Dict[str, float] = {
    "CRITICAL": 4.0,
    "HIGH": 3.0,
    "MEDIUM": 2.0,
    "LOW": 1.0,
}


@dataclass
class HygieneFinding:
    finding_id: str
    category: str  # "duplicate_node" | "orphan_clutter" | "temporal_contradiction" | "stale_assertion"
    severity: str  # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    target_entities: List[str]
    description: str
    proposed_fix: str
    recurrence_count: int = 1
    evidence_missing: bool = False
    generation_id: str = "gen_0"
    evidence_event_ids: Tuple[str, ...] = field(default_factory=tuple)
    predicate: Optional[str] = None
    is_multi_valued: bool = False
    validity_interval: Optional[Tuple[str, str]] = None
    disposition: str = "PENDING"

    def unique_recurrence_count(self) -> int:
        """Computes verified event recurrence: duplicate event IDs never multiply mass."""
        if self.evidence_event_ids:
            return len(set(self.evidence_event_ids))
        return max(1, self.recurrence_count)


@dataclass
class QueryImpactRecord:
    entity_id: str
    query_frequency: int
    failure_rate: float = 0.0
    mrr_drop: float = 0.0
    affected_query_count: int = 1
    generation_id: str = "gen_0"
    window_id: str = "window_default"
    query_denominator: int = 1
    observed_query_ids: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class RankedMaintenanceProposal:
    proposal_id: str
    harm_score: float
    target_entities: List[str]
    category: str
    severity: str
    recurrence_count: int
    query_impact_score: float
    artifact: Dict[str, Any]
    has_conflicts: bool = False
    conflict_details: Optional[str] = None
    generation_id: str = "gen_0"
    disposition: str = "GOVERNED_FIX"
    is_deferred: bool = False


@dataclass
class MaintenancePlanReport:
    total_findings: int
    deduplicated_count: int
    proposals: List[RankedMaintenanceProposal]
    is_read_only: bool = True
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def total_harm_score(self) -> float:
        """Sum of harm scores across all ranked maintenance proposals."""
        return round(sum(p.harm_score for p in self.proposals), 4)


def _extract_max_severity(group: List[HygieneFinding]) -> str:
    severities = {f.severity.upper() for f in group}
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if sev in severities:
            return sev
    return "LOW"


def _determine_proposal_action(
    group: List[HygieneFinding],
    has_conflicts: bool,
    proposed_fixes: List[str],
    entities: List[str],
) -> Tuple[str, bool, str, str]:
    if all(f.evidence_missing for f in group):
        return ("DEFERRED", True, f"Defer mutation and acquire discriminating evidence for {entities}.", "acquire_discriminating_evidence")
    if any(f.is_multi_valued for f in group):
        return ("COEXISTENCE", False, f"Retain multi-valued coexistence for {entities}; destructive supersession barred.", "retain_coexistence")
    if has_conflicts:
        return ("CONTRADICTION", False, f"Resolve conflicting maintenance fixes {proposed_fixes} for {entities}.", proposed_fixes[0] if proposed_fixes else "manual_review")
    fix_str = proposed_fixes[0] if proposed_fixes else "manual_review"
    return "GOVERNED_FIX", False, f"Execute governed maintenance fix '{fix_str}' for {entities}.", fix_str


class HarmPrioritizedPlanner:
    """Read-only planner that joins hygiene findings with query impact to rank repairs."""

    is_read_only: bool = True

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or "run-maintenance-planner"

    def compute_query_impact(
        self,
        target_entities: Sequence[str],
        query_impact_index: Dict[str, QueryImpactRecord],
        finding_generation_id: str = "gen_0",
    ) -> float:
        """Calculates combined query impact score across target entities, rejecting cross-gen joins."""
        matched: List[QueryImpactRecord] = []
        for ent in target_entities:
            normalized = ent.strip().lower()
            if normalized in query_impact_index:
                rec = query_impact_index[normalized]
                if rec.generation_id != finding_generation_id:
                    logger.warning(
                        "CROSS_GEN_JOIN_REJECTED: Finding gen '%s' != impact gen '%s' for '%s'.",
                        finding_generation_id, rec.generation_id, ent,
                    )
                    continue
                matched.append(rec)

        if not matched:
            return 0.1  # Minimal baseline impact for unqueried entities

        total = 0.0
        for rec in matched:
            eff_failure = min(1.0, max(0.0, float(rec.failure_rate)))
            eff_mrr = min(1.0, max(0.0, float(rec.mrr_drop)))
            denom = max(1, rec.query_denominator)
            freq_factor = min(1.0, max(0.0, float(rec.query_frequency) / denom))
            total += freq_factor * (1.0 + eff_failure + eff_mrr)
        return round(total, 4)

    def calculate_harm_score(
        self,
        severity: str,
        query_impact_score: float,
        recurrence_count: int,
    ) -> float:
        """Harm Score = Severity * (1.0 + Query Impact) * Recurrence Factor."""
        sev_weight = SEVERITY_WEIGHTS.get(severity.upper(), 1.0)
        recurrence_factor = 1.0 + 0.5 * min(max(recurrence_count - 1, 0), 4)
        harm = sev_weight * (1.0 + query_impact_score) * recurrence_factor
        return round(harm, 4)

    def _cluster_key(self, entities: Sequence[str]) -> str:
        """Deterministic grouping key for deduplicating findings on the same entity set."""
        sorted_ents = sorted([e.strip().lower() for e in entities if e.strip()])
        return ":".join(sorted_ents) if sorted_ents else "unknown_cluster"

    def deduplicate_findings(
        self, findings: Sequence[HygieneFinding]
    ) -> List[Tuple[str, List[HygieneFinding]]]:
        """Groups findings by affected entity cluster to eliminate redundant proposals."""
        grouped: Dict[str, List[HygieneFinding]] = {}
        for f in findings:
            key = self._cluster_key(f.target_entities)
            grouped.setdefault(key, []).append(f)
        return list(grouped.items())

    def _build_ranked_proposal(
        self,
        cluster_key: str,
        group: List[HygieneFinding],
        impact_index: Dict[str, QueryImpactRecord],
        active_runs: List[str],
    ) -> RankedMaintenanceProposal:
        first = group[0]
        entities = first.target_entities
        category = first.category

        max_sev = _extract_max_severity(group)
        total_recurrence = sum(f.unique_recurrence_count() for f in group)
        finding_gen = first.generation_id
        q_impact = self.compute_query_impact(entities, impact_index, finding_generation_id=finding_gen)
        harm = self.calculate_harm_score(max_sev, q_impact, total_recurrence)

        # Detect conflicting fixes and determine disposition
        proposed_fixes = list({f.proposed_fix for f in group if f.proposed_fix})
        has_conflicts = len(proposed_fixes) > 1
        conflict_details = (
            f"Conflicting proposed fixes: {proposed_fixes}" if has_conflicts else None
        )

        disposition, is_deferred, change_desc, fix_str = _determine_proposal_action(
            group, has_conflicts, proposed_fixes, entities
        )

        proposal_id = stable_id("learning-proposal", "maintenance", cluster_key, length=24)
        evidence_events = [f.finding_id for f in group]
        risk_level = "high" if max_sev in ("CRITICAL", "HIGH") or has_conflicts else ("medium" if max_sev == "MEDIUM" else "low")

        replay_def = {
            "planner": "HarmPrioritizedPlanner.plan_maintenance",
            "cluster_key": cluster_key,
            "proposed_fix": fix_str,
            "rollback_action": "REVERT_MAINTENANCE_MUTATION",
        }
        prov = build_signed_provenance(
            proposal_id=proposal_id,
            evidence_event_ids=evidence_events,
            run_ids=active_runs,
            proposed_change=change_desc,
            replay_definition=replay_def,
        )

        ext = {
            "com.partner_advisory.nee.maintenance": {
                "cluster_key": cluster_key,
                "harm_score": harm,
                "target_entities": entities,
                "category": category,
                "query_impact_score": q_impact,
                "deduplicated_findings_count": len(group),
                "has_conflicts": has_conflicts,
                "generation_id": finding_gen,
                "disposition": disposition,
            },
            PROVENANCE_EXTENSION_KEY: prov,
        }

        artifact = create_artifact(
            "learning-change-proposal",
            proposal_id,
            proposal_id=proposal_id,
            run_ids=active_runs,
            proposal_type="workflow",
            status="proposed",
            problem=f"Observed graph degradation on {entities} (Harm Score: {harm:.2f}).",
            evidence_event_ids=evidence_events,
            proposed_change=change_desc,
            evaluation_plan=[
                "Simulate graph consolidation in isolated sandbox.",
                "Verify retrieval MRR and nDCG regression parity across benchmark queries.",
                "Require human operator sign-off in HITL ledger.",
            ],
            risk=risk_level,
            approval_required=True,
            auto_apply=False,
            created_at=datetime.now(timezone.utc).isoformat(),
            extensions=ext,
        )

        return RankedMaintenanceProposal(
            proposal_id=proposal_id,
            harm_score=harm,
            target_entities=entities,
            category=category,
            severity=max_sev,
            recurrence_count=total_recurrence,
            query_impact_score=q_impact,
            artifact=artifact,
            has_conflicts=has_conflicts,
            conflict_details=conflict_details,
            generation_id=finding_gen,
            disposition=disposition,
            is_deferred=is_deferred,
        )

    def plan_maintenance(
        self,
        hygiene_findings: Sequence[HygieneFinding],
        query_impacts: Sequence[QueryImpactRecord] = (),
        run_ids: Optional[Sequence[str]] = None,
    ) -> MaintenancePlanReport:
        """Constructs ranked, deduplicated maintenance proposals adhering to contract."""
        impact_index = {q.entity_id.strip().lower(): q for q in query_impacts}
        active_runs = sorted(list(set(run_ids or [self.run_id])))
        grouped_findings = self.deduplicate_findings(hygiene_findings)

        proposals: List[RankedMaintenanceProposal] = []
        total_findings = len(hygiene_findings)

        for cluster_key, group in grouped_findings:
            proposals.append(self._build_ranked_proposal(cluster_key, group, impact_index, active_runs))

        # Rank strictly by descending harm score
        proposals.sort(key=lambda p: p.harm_score, reverse=True)

        dedup_count = total_findings - len(proposals)

        return MaintenancePlanReport(
            total_findings=total_findings,
            deduplicated_count=dedup_count,
            proposals=proposals,
            is_read_only=True,
        )


def _parse_entity_targets(raw_val: Any) -> List[str]:
    if not raw_val:
        return []
    import json
    try:
        p = json.loads(raw_val) if isinstance(raw_val, str) else raw_val
        if isinstance(p, list):
            return [str(x).strip().lower() for x in p if str(x).strip()]
        return [str(p).strip().lower()]
    except Exception:
        return [str(raw_val).strip().lower()]


def _index_answer_records(cur: Any) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    cur.execute("SELECT answer_id, retrieval_seeds, cited_assertion_ids FROM answer_records")
    for ans_id, seeds, cites in cur.fetchall():
        for t in set(_parse_entity_targets(seeds) + _parse_entity_targets(cites)):
            rec = records.setdefault(t, {"freq": 0, "fails": 0, "qids": set()})
            rec["freq"] += 1
            rec["qids"].add(ans_id)
    return records


def _apply_feedback_failures(cur: Any, records: Dict[str, Dict[str, Any]]) -> None:
    cur.execute("SELECT answer_id, feedback_type, rating FROM answer_feedback")
    for ans_id, fb_type, rating in cur.fetchall():
        is_neg = str(rating).lower() in ("negative", "poor", "1", "false") or "fail" in str(fb_type).lower()
        if not is_neg:
            continue
        for d in records.values():
            if ans_id in d["qids"]:
                d["fails"] += 1


def extract_query_impact_records_from_ledger(
    ledger_path: Path | str,
    generation_id: str = "gen_0",
) -> List[QueryImpactRecord]:
    """Extracts empirical QueryImpactRecord instances from answer_ledger.sqlite."""
    p = Path(ledger_path)
    if not p.is_file():
        return []
    try:
        from storage.connection import create_sqlite_conn
        with create_sqlite_conn(p, readonly=True) as conn:
            cur = conn.cursor()
            tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            records = _index_answer_records(cur) if "answer_records" in tables else {}
            if "answer_feedback" in tables and records:
                _apply_feedback_failures(cur, records)
    except Exception as exc:
        logger.warning("Failed extracting query impacts from ledger: %s", exc)
        return []

    return [
        QueryImpactRecord(
            entity_id=ent,
            query_frequency=d["freq"],
            failure_rate=round((d["fails"] / d["freq"]) if d["freq"] else 0.0, 4),
            mrr_drop=0.20 if d["fails"] else 0.0,
            affected_query_count=max(1, d["fails"]),
            generation_id=generation_id,
        )
        for ent, d in sorted(records.items())
    ]

