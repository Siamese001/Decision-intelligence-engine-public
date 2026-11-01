"""
dual_pipeline/clearance_lifecycle_core/phase2_clearance.py

Phase 2: Post-Session / Pre-U0 HITL Clearance Queue.
Executes atomic, confidence-scored human-in-the-loop disambiguation
across all 5 domains, updates Bayesian priors, and clears C-10 locks.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dual_pipeline.ambiguity_core.models import AmbiguityDomain, AmbiguityRecord, AmbiguityStatus
from dual_pipeline.hitl_governance_core.telemetry import resolve_hitl_ledger_path
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

from .c10_register import AmbiguityLockRegister

logger = logging.getLogger(__name__)


class Phase2ClearanceCoordinator:
    """Post-Session atomic HITL clearance coordinator before Stage U0 admission."""

    def __init__(
        self,
        register: AmbiguityLockRegister,
        ledger_path: Optional[Path] = None,
        hitl_callback: Optional[Callable[[AmbiguityRecord], str]] = None,
        fail_closed_on_unhandled: bool = False,
    ) -> None:
        self.register = register
        self.ledger_path = ledger_path or resolve_hitl_ledger_path()
        self.hitl_callback = hitl_callback
        self.fail_closed_on_unhandled = fail_closed_on_unhandled

    def _persist_hitl_decision(
        self,
        record: AmbiguityRecord,
        selected_option_id: str,
        rationale: str,
    ) -> None:
        """Persists resolved operator decision into the governed decision ledger."""
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "ambiguity_id": record.ambiguity_id,
            "domain": record.domain.value,
            "segment_id": record.segment_id,
            "selected_option_id": selected_option_id,
            "rationale": rationale,
            "candidates": [c.model_dump() for c in record.candidates],
            "delta_margin": record.delta_margin,
        }

        if self.ledger_path is not None:
            try:
                data = {"decisions": [], "priors": {}}
                if self.ledger_path.exists():
                    try:
                        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                data.setdefault("decisions", []).append(entry)

                priors = data.setdefault("priors", {})
                domain_priors = priors.setdefault(record.domain.value, {})
                sel_cand = next((c for c in record.candidates if c.option_id == selected_option_id), None)
                if sel_cand:
                    cur_count = domain_priors.get(sel_cand.text, 0)
                    domain_priors[sel_cand.text] = min(10, cur_count + 1)

                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                self.ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                logger.info(f"HITL decision persistently logged to {self.ledger_path} for {record.ambiguity_id}.")
            except Exception as e:
                logger.warning(f"Failed to persist HITL decision to ledger: {e}")

        try:
            from dual_pipeline.hitl_governance import HITLDecisionStore
            store = HITLDecisionStore()
            store.record_decision(
                entry_id=record.ambiguity_id,
                category=record.domain.value,
                prompt=f"Resolution for {record.ambiguity_id} in {record.segment_id}",
                options=[c.model_dump() for c in record.candidates],
                selected_option=selected_option_id,
                selected_key=selected_option_id,
                rationale=rationale,
                operator_id="phase2_clearance",
                delta_margin=record.delta_margin,
            )
        except Exception as e:
            logger.debug(f"HITLDecisionStore dual-write note: {e}")

    def clear_queue(
        self,
        records: List[AmbiguityRecord],
        auto_resolve_high_margin: bool = True,
    ) -> List[AmbiguityRecord]:
        """Processes the clearance queue atomically one item at a time."""
        cleared_records: List[AmbiguityRecord] = []
        lock_map = {lock.ambiguity_id: lock for lock in self.register.entries}

        for record in records:
            lock = lock_map.get(record.ambiguity_id)
            if not lock or lock.status != "OPEN":
                cleared_records.append(record)
                continue

            if not record.requires_hitl and auto_resolve_high_margin:
                _resolve_autonomous(record, lock, self.register)
            else:
                _apply_hitl_resolution(
                    record,
                    lock,
                    self.register,
                    self.hitl_callback,
                    self.fail_closed_on_unhandled,
                    self._persist_hitl_decision,
                )
            cleared_records.append(record)

        return cleared_records

    def apply_resolutions_to_transcript(
        self,
        turns: List[Dict[str, Any]],
        resolved_records: List[AmbiguityRecord],
    ) -> List[Dict[str, Any]]:
        """Applies cleared ambiguity resolutions directly into transcript turns, supporting multiple corrections per segment."""
        from collections import defaultdict
        resolved_by_seg = defaultdict(list)
        for r in resolved_records:
            if r.selected_option_id:
                resolved_by_seg[r.segment_id].append(r)

        updated_turns = []
        for idx, turn in enumerate(turns):
            turn_copy = dict(turn)
            recs = resolved_by_seg.get(f"turn_{idx}", [])
            for rec in recs:
                _apply_resolution_to_turn(turn_copy, rec)
            updated_turns.append(turn_copy)

        return updated_turns


def _resolve_autonomous(
    record: AmbiguityRecord,
    lock: Any,
    register: AmbiguityLockRegister,
) -> None:
    top_cand = record.candidates[0] if record.candidates else None
    sel_id = top_cand.option_id if top_cand else "none"
    record.status = AmbiguityStatus.RESOLVED_AUTONOMOUS
    record.selected_option_id = sel_id
    record.resolution_rationale = (
        f"Autonomous resolution: Delta margin {record.delta_margin:.2f} > {get_hitl_ambiguity_margin_threshold():.2f} threshold."
    )
    record.resolved_by = "autonomous_margin_gate"
    record.resolved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    register.clear_lock(
        lock_id=lock.lock_id,
        resolution_payload={"selected": sel_id, "mode": "AUTONOMOUS", "margin": record.delta_margin},
        status="CLEARED",
    )


def _determine_hitl_choice(
    record: AmbiguityRecord,
    hitl_callback: Optional[Callable[[AmbiguityRecord], str]],
    fail_closed: bool,
) -> tuple[str, AmbiguityStatus, str, str]:
    chosen = hitl_callback(record) if hitl_callback else None
    if not chosen:
        if fail_closed:
            raise RuntimeError(
                f"HITL resolution failed closed: unhandled ambiguity {record.ambiguity_id} "
                f"with margin {record.delta_margin:.2f} <= {get_hitl_ambiguity_margin_threshold():.2f} and no interactive callback provided."
            )
        recommended = next((c for c in record.candidates if c.is_recommended), None)
        fallback_id = recommended.option_id if recommended else (
            record.candidates[0].option_id if record.candidates else "unresolved"
        )
        if record.domain == AmbiguityDomain.D6_ENTITY_CLARIFICATION:
            return (
                fallback_id,
                AmbiguityStatus.UNRESOLVED_PENDING_OPERATOR,
                f"Unresolved referenced entity '{record.ambiguity_id}': no interactive callback provided; marked for operator review.",
                "pending_operator",
            )
        return (
            fallback_id,
            AmbiguityStatus.UNRESOLVED_EXPLICIT,
            "Unresolved: no interactive operator callback; defaulted to recommendation for unblocking.",
            "fallback_heuristic",
        )
    return (
        chosen,
        AmbiguityStatus.RESOLVED_HITL,
        "Resolved via authentic operator HITL clearance callback.",
        "operator",
    )


def _apply_hitl_resolution(
    record: AmbiguityRecord,
    lock: Any,
    register: AmbiguityLockRegister,
    hitl_callback: Optional[Callable[[AmbiguityRecord], str]],
    fail_closed: bool,
    persist_fn: Callable[[AmbiguityRecord, str, str], None],
) -> None:
    logger.info(
        f"Surfacing atomic HITL decision for {record.ambiguity_id} ({record.domain.value}) "
        f"with margin {record.delta_margin:.2f} <= {get_hitl_ambiguity_margin_threshold():.2f}"
    )
    chosen_id, status, rationale, resolved_by = _determine_hitl_choice(
        record, hitl_callback, fail_closed
    )
    record.status = status
    record.selected_option_id = chosen_id
    record.resolution_rationale = rationale
    record.resolved_by = resolved_by
    record.resolved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    persist_fn(record, chosen_id, rationale)
    mode = "HITL" if status == AmbiguityStatus.RESOLVED_HITL else "FALLBACK"
    register.clear_lock(
        lock_id=lock.lock_id,
        resolution_payload={"selected": chosen_id, "mode": mode},
        status="CLEARED",
    )


def _apply_d1_speaker_resolution(turn: Dict[str, Any], new_speaker: str) -> None:
    old_speaker = turn.get("speaker", "")
    turn["speaker"] = new_speaker
    text_str = turn.get("text", "")
    if old_speaker and text_str.startswith(f"{old_speaker}:"):
        turn["text"] = f"{new_speaker}:{text_str[len(old_speaker)+1:]}"


def _apply_d2_vocab_resolution(turn: Dict[str, Any], new_vocab: str, raw_text: str) -> None:
    if raw_text:
        turn["text"] = turn.get("text", "").replace(raw_text, new_vocab)


def _apply_d6_entity_resolution(
    turn: Dict[str, Any], sel_cand: Any, ambiguity_id: str
) -> None:
    tok = sel_cand.attributes.get("token") or ambiguity_id
    c_name = sel_cand.attributes.get("canonical_name") or sel_cand.text
    if tok and c_name and tok != c_name:
        import re
        turn["text"] = re.sub(rf"\b{re.escape(tok)}\b", c_name, turn.get("text", ""))


def _apply_resolution_to_turn(turn: Dict[str, Any], rec: AmbiguityRecord) -> None:
    sel_cand = next((c for c in rec.candidates if c.option_id == rec.selected_option_id), None)
    if not sel_cand:
        return
    d_val = rec.domain.value if hasattr(rec.domain, "value") else str(rec.domain)
    if d_val == "D1_SPEAKER_ATTRIBUTION":
        _apply_d1_speaker_resolution(turn, sel_cand.text)
    elif d_val == "D2_DOMAIN_VOCABULARY":
        raw_text = rec.candidates[1].text if len(rec.candidates) > 1 else ""
        _apply_d2_vocab_resolution(turn, sel_cand.text, raw_text)
    elif d_val == "D5_DROPS_AND_GAPS" and "Rule C-11" in sel_cand.text:
        turn["text"] = f"{turn.get('text', '')} {sel_cand.text}".strip()
    elif d_val == "D6_ENTITY_CLARIFICATION":
        amb_id = getattr(rec, "ambiguity_id", "")
        _apply_d6_entity_resolution(turn, sel_cand, amb_id)

