"""Proactive HITL update pusher & Knowledge Graph Studio queue bridge.

Translates stale edge audits, roster set differences, and transition signals
into deduplicated SQLite validation_queue items and Studio-ready action cards.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from storage.connection import create_sqlite_conn
from unified_pipeline.temporal_reconciliation.receptive_watchlist_injector import TransitionSignal
from unified_pipeline.temporal_reconciliation.set_diff_reconciler import RoleRetractionCandidate
from unified_pipeline.temporal_reconciliation.staleness_auditor import StalenessRecord


@dataclass(frozen=True)
class StudioActionOption:
    """Actionable choice on an interactive Studio card."""

    option_id: str
    label: str
    action_type: str  # 'RETIRE', 'REPLACE', 'RE_ATTEST'
    suggested_replacement: Optional[str]
    effective_date: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StudioPushCard:
    """Card presented in Knowledge Graph Studio for operator action."""

    card_id: str
    target_edge_id: str
    person_node_id: str
    person_name: str
    workstream_node_id: str
    workstream_name: str
    edge_type: str
    priority: str
    age_days: int
    trigger_source: str  # 'STALENESS_TTL', 'ROSTER_SET_DIFF', 'TRANSCRIPT_SIGNAL'
    evidence_summary: str
    options: List[StudioActionOption]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "card_id": self.card_id,
            "target_edge_id": self.target_edge_id,
            "person_node_id": self.person_node_id,
            "person_name": self.person_name,
            "workstream_node_id": self.workstream_node_id,
            "workstream_name": self.workstream_name,
            "edge_type": self.edge_type,
            "priority": self.priority,
            "age_days": self.age_days,
            "trigger_source": self.trigger_source,
            "evidence_summary": self.evidence_summary,
            "options": [opt.to_dict() for opt in self.options],
        }


class ProactiveUpdatePusher:
    """Pushes actionable stale updates into validation_queue and Studio queue."""

    def __init__(self, db_path: Path | str, queue_owner: str = "OPERATOR") -> None:
        raw_p = Path(db_path) if isinstance(db_path, str) else db_path
        self.db_path = raw_p.resolve()
        self.owner = queue_owner

    def push_stale_records(
        self,
        records: Sequence[StalenessRecord],
        reference_date: Optional[str | date] = None,
    ) -> List[StudioPushCard]:
        ref_dt = self._resolve_date(reference_date)
        due_str = (ref_dt + timedelta(days=7)).isoformat()
        now_iso = f"{ref_dt.isoformat()}T00:00:00Z"

        conn = create_sqlite_conn(self.db_path, readonly=False)
        cards: List[StudioPushCard] = []
        try:
            for rec in records:
                if rec.staleness_tier not in ("P1", "P2"):
                    continue
                q_text = (
                    f"Confirm tenure: {rec.from_node_name} as {rec.edge_type} on "
                    f"{rec.to_node_name} (unattested for {rec.age_days}d since {rec.source_date})."
                )
                self._insert_validation_item_if_absent(
                    conn=conn,
                    node_id=rec.from_node_id,
                    edge_id=rec.edge_id,
                    question=q_text,
                    priority=rec.staleness_tier,
                    due_date=due_str,
                    created_at=now_iso,
                )
                cards.append(self._create_card_from_staleness(rec, ref_dt))
            conn.commit()
        finally:
            conn.close()
        return cards

    def push_retraction_candidates(
        self,
        candidates: Sequence[RoleRetractionCandidate],
        reference_date: Optional[str | date] = None,
    ) -> List[StudioPushCard]:
        ref_dt = self._resolve_date(reference_date)
        due_str = (ref_dt + timedelta(days=5)).isoformat()
        now_iso = f"{ref_dt.isoformat()}T00:00:00Z"

        conn = create_sqlite_conn(self.db_path, readonly=False)
        cards: List[StudioPushCard] = []
        try:
            for cand in candidates:
                q_text = (
                    f"Roster roll-off: {cand.missing_entity_name} omitted from "
                    f"{cand.retracting_source_name} on {cand.workstream_name}. Retire active edge?"
                )
                self._insert_validation_item_if_absent(
                    conn=conn,
                    node_id=cand.missing_entity_id,
                    edge_id=cand.target_edge_id,
                    question=q_text,
                    priority="P1",
                    due_date=due_str,
                    created_at=now_iso,
                )
                cards.append(self._create_card_from_candidate(cand, ref_dt))
            conn.commit()
        finally:
            conn.close()
        return cards

    def push_transition_signals(
        self,
        signals: Sequence[TransitionSignal],
        reference_date: Optional[str | date] = None,
    ) -> List[StudioPushCard]:
        ref_dt = self._resolve_date(reference_date)
        due_str = (ref_dt + timedelta(days=3)).isoformat()
        now_iso = f"{ref_dt.isoformat()}T00:00:00Z"

        conn = create_sqlite_conn(self.db_path, readonly=False)
        cards: List[StudioPushCard] = []
        try:
            for sig in signals:
                repl = f" (replacement: {sig.replacement_name})" if sig.replacement_name else ""
                q_text = (
                    f"Transcript transition{repl}: '{sig.verbatim_quote}'. "
                    f"Retire/supersede edge on {sig.workstream_name}?"
                )
                self._insert_validation_item_if_absent(
                    conn=conn,
                    node_id=sig.retracted_entity_id,
                    edge_id=sig.supersedes_edge_id,
                    question=q_text,
                    priority="P1",
                    due_date=due_str,
                    created_at=now_iso,
                )
                cards.append(self._create_card_from_signal(sig, ref_dt))
            conn.commit()
        finally:
            conn.close()
        return cards

    def _insert_validation_item_if_absent(
        self,
        conn: Any,
        node_id: str,
        edge_id: str,
        question: str,
        priority: str,
        due_date: str,
        created_at: str,
    ) -> bool:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM validation_queue WHERE edge_id = ? AND status = 'open' LIMIT 1;",
            (edge_id,),
        )
        if cur.fetchone():
            return False

        val_id = f"val_stale_{uuid.uuid4().hex[:10]}"
        cur.execute(
            """
            INSERT INTO validation_queue (
                validation_id, node_id, edge_id, claim_id, validation_question,
                owner, priority, due_date, status, resolution, resolved_at, created_at
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 'open', NULL, NULL, ?)
            """,
            (val_id, node_id, edge_id, question, self.owner, priority, due_date, created_at),
        )
        return True

    def _resolve_date(self, ref: Optional[str | date]) -> date:
        if isinstance(ref, date):
            return ref
        if isinstance(ref, str):
            return date.fromisoformat(ref.strip()[:10])
        return date.today()

    def _create_card_from_staleness(self, rec: StalenessRecord, ref_dt: date) -> StudioPushCard:
        opts = [
            StudioActionOption(
                option_id=f"opt_retire_{uuid.uuid4().hex[:6]}",
                label=f"Retire role on {rec.to_node_name}",
                action_type="RETIRE",
                suggested_replacement=None,
                effective_date=ref_dt.isoformat(),
            ),
            StudioActionOption(
                option_id=f"opt_reattest_{uuid.uuid4().hex[:6]}",
                label="Re-attest active as of today",
                action_type="RE_ATTEST",
                suggested_replacement=None,
                effective_date=ref_dt.isoformat(),
            ),
        ]
        return StudioPushCard(
            card_id=f"card_{rec.edge_id}",
            target_edge_id=rec.edge_id,
            person_node_id=rec.from_node_id,
            person_name=rec.from_node_name,
            workstream_node_id=rec.to_node_id,
            workstream_name=rec.to_node_name,
            edge_type=rec.edge_type,
            priority=rec.staleness_tier,
            age_days=rec.age_days,
            trigger_source="STALENESS_TTL",
            evidence_summary=f"Unattested for {rec.age_days}d (last in {rec.source_name})",
            options=opts,
        )

    def _create_card_from_candidate(
        self, cand: RoleRetractionCandidate, ref_dt: date
    ) -> StudioPushCard:
        opts = [
            StudioActionOption(
                option_id=f"opt_retire_{uuid.uuid4().hex[:6]}",
                label=f"Confirm roll-off as of {cand.retracting_source_date}",
                action_type="RETIRE",
                suggested_replacement=None,
                effective_date=cand.retracting_source_date,
            ),
            StudioActionOption(
                option_id=f"opt_ignore_{uuid.uuid4().hex[:6]}",
                label="Keep active (partial omission)",
                action_type="RE_ATTEST",
                suggested_replacement=None,
                effective_date=ref_dt.isoformat(),
            ),
        ]
        return StudioPushCard(
            card_id=f"card_{cand.target_edge_id}",
            target_edge_id=cand.target_edge_id,
            person_node_id=cand.missing_entity_id,
            person_name=cand.missing_entity_name,
            workstream_node_id=cand.workstream_node_id,
            workstream_name=cand.workstream_name,
            edge_type=cand.edge_type,
            priority="P1",
            age_days=0,
            trigger_source="ROSTER_SET_DIFF",
            evidence_summary=cand.retraction_reason,
            options=opts,
        )

    def _create_card_from_signal(self, sig: TransitionSignal, ref_dt: date) -> StudioPushCard:
        opts: List[StudioActionOption] = []
        if sig.replacement_name:
            opts.append(
                StudioActionOption(
                    option_id=f"opt_replace_{uuid.uuid4().hex[:6]}",
                    label=f"Replace with {sig.replacement_name}",
                    action_type="REPLACE",
                    suggested_replacement=sig.replacement_name,
                    effective_date=ref_dt.isoformat(),
                )
            )
        opts.append(
            StudioActionOption(
                option_id=f"opt_retire_{uuid.uuid4().hex[:6]}",
                label=f"Retire {sig.retracted_entity_name} from {sig.workstream_name}",
                action_type="RETIRE",
                suggested_replacement=None,
                effective_date=ref_dt.isoformat(),
            )
        )
        return StudioPushCard(
            card_id=f"card_{sig.supersedes_edge_id}",
            target_edge_id=sig.supersedes_edge_id,
            person_node_id=sig.retracted_entity_id,
            person_name=sig.retracted_entity_name,
            workstream_node_id=sig.workstream_node_id,
            workstream_name=sig.workstream_name,
            edge_type="TRANSITION",
            priority="P1",
            age_days=0,
            trigger_source="TRANSCRIPT_SIGNAL",
            evidence_summary=f"Quote: '{sig.verbatim_quote}'",
            options=opts,
        )


def export_studio_queue(cards: Sequence[StudioPushCard], target_path: Path | str) -> Path:
    out = Path(target_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": date.today().isoformat(),
        "total_cards": len(cards),
        "cards": [c.to_dict() for c in cards],
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
