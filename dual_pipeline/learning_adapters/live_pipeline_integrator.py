"""
dual_pipeline/learning_adapters/live_pipeline_integrator.py

Layer 5 Live Pipeline Integrator attaching governed outcome recording to
extraction, retrieval, decision, and intake workflows.
Captures failure telemetry, binds cryptographic provenance, tracks durable
cursors across restarts, and drains normalized cohort items into learning engine.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    AgentOutcomeEvent,
    OutcomeType,
)

logger = logging.getLogger(__name__)
DEFAULT_LEDGER_DIR = ROOT / ".local" / "ledger"


class LivePipelineIntegrator:
    """Thread-safe live pipeline outcome recorder with durable cursor tracking."""

    def __init__(
        self,
        runtime_root: Optional[Path] = None,
        events_log_path: Optional[Path] = None,
        cursor_path: Optional[Path] = None,
    ) -> None:
        self.runtime_root = (runtime_root or ROOT / ".local").resolve()
        ledger_dir = self.runtime_root / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        self.events_log_path = events_log_path or (ledger_dir / "live_outcome_events.jsonl")
        self.cursor_path = cursor_path or (ledger_dir / "live_outcomes_cursor.json")
        self.adapter = AgentOutcomeAdapter()
        self._lock = threading.Lock()
        self._events: List[AgentOutcomeEvent] = []

    def _load_cursor(self) -> Dict[str, Any]:
        """Loads cursor offset and processed IDs from durable storage."""
        if self.cursor_path.is_file():
            try:
                with open(self.cursor_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load cursor from %s: %s", self.cursor_path, e)
        return {"last_processed_line": 0, "last_processed_byte_offset": 0, "processed_event_ids": []}

    def _save_cursor(self, last_line: int, processed_ids: Sequence[str], last_byte_offset: int = 0) -> None:
        """Atomically persists cursor offset (line + byte offset) and recent processed event IDs."""
        try:
            data = {
                "last_processed_line": last_line,
                "last_processed_byte_offset": last_byte_offset,
                "processed_event_ids": list(processed_ids)[-1000:],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = self.cursor_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.cursor_path)
        except Exception as e:
            logger.warning("Failed to save cursor to %s: %s", self.cursor_path, e)

    def _event_from_dict(self, d: Dict[str, Any]) -> Optional[AgentOutcomeEvent]:
        """Safely deserializes an AgentOutcomeEvent from JSON dictionary."""
        try:
            return AgentOutcomeEvent(
                event_id=d["event_id"],
                run_id=d.get("run_id", "run-unknown"),
                trace_id=d.get("trace_id", ""),
                span_id=d.get("span_id", ""),
                outcome_type=OutcomeType(d["outcome_type"]),
                source_agent=d.get("source_agent", "unknown"),
                stage=d.get("stage", "L4"),
                category=d.get("category", "unknown"),
                observed_outcome=d.get("observed_outcome", {}),
                confidence=float(d.get("confidence", 1.0)),
                expected_or_corrected_outcome=d.get("expected_or_corrected_outcome"),
                provenance_receipt_id=d.get("provenance_receipt_id"),
                status=d.get("status", "FAIL"),
                created_at=d.get("created_at"),
                extensions=d.get("extensions", {}),
            )
        except Exception as err:
            logger.warning("Failed to deserialize outcome event: %s", err)
            return None

    def _parse_log_line(self, line: str) -> Optional[AgentOutcomeEvent]:
        line_str = line.strip()
        if not line_str:
            return None
        try:
            return self._event_from_dict(json.loads(line_str))
        except Exception as err:
            logger.warning("Skipping corrupted event line: %s", err)
            return None

    def record_outcome(self, event: AgentOutcomeEvent) -> AgentOutcomeEvent:
        """Stores an outcome event in-memory and appends to persistent jsonl log."""
        with self._lock:
            self._events.append(event)
            try:
                line = json.dumps(event.to_dict(), sort_keys=True) + "\n"
                with open(self.events_log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as err:
                logger.warning("Failed to persist live outcome event: %s", err)
        return event

    def record_success(
        self,
        source_agent: str,
        category: str,
        stage: str = "L4",
        details: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
        denominator: int = 1,
        created_at: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Records a successful execution outcome with authentic denominator."""
        obs = {"status": "SUCCESS", "denominator": max(1, int(denominator)), **(dict(details) if details else {})}
        evt = self.adapter.create_outcome_event(
            outcome_type=OutcomeType.QUALITY_CHECK_FAILURE,
            source_agent=source_agent,
            stage=stage,
            category=category,
            observed_outcome=obs,
            confidence=1.0,
            status="PASS",
            run_id=run_id,
            provenance_receipt_id=receipt_id,
            created_at=created_at,
        )
        return self.record_outcome(evt)

    def record_abstention(
        self,
        source_agent: str,
        category: str,
        reason: str,
        stage: str = "L4",
        details: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
        denominator: int = 1,
        created_at: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Records an intentional abstention event with reason and authentic denominator."""
        obs = {
            "status": "ABSTAIN",
            "abstention_reason": reason,
            "denominator": max(1, int(denominator)),
            **(dict(details) if details else {}),
        }
        evt = self.adapter.create_outcome_event(
            outcome_type=OutcomeType.QUALITY_CHECK_FAILURE,
            source_agent=source_agent,
            stage=stage,
            category=category,
            observed_outcome=obs,
            confidence=1.0,
            status="NOOP",
            run_id=run_id,
            provenance_receipt_id=receipt_id,
            created_at=created_at,
        )
        return self.record_outcome(evt)

    def record_adjudication_error(
        self,
        candidate_a: str,
        candidate_b: str,
        conflict_type: str = "contradiction",
        details: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
        source_agent: str = "AdjudicationJudgeAgent",
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Hook for extraction adjudication errors."""
        evt = self.adapter.build_adjudication_error_event(
            conflicting_entities=[candidate_a, candidate_b],
            reason=conflict_type,
            candidate_id=candidate_a,
            corrected_canonical=candidate_b,
            source_agent=source_agent,
            run_id=run_id,
            receipt_id=receipt_id,
        )
        return self.record_outcome(evt)

    def record_retrieval_miss(
        self,
        query: str,
        missing_requirements: Sequence[str],
        coverage_score: float = 0.0,
        run_id: Optional[str] = None,
        source_agent: str = "RetrievalCoordinator",
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Hook for retrieval coverage misses."""
        evt = self.adapter.build_retrieval_miss_event(
            query_text=query,
            missing_requirements=list(missing_requirements),
            source_agent=source_agent,
            run_id=run_id,
            receipt_id=receipt_id,
        )
        return self.record_outcome(evt)

    def record_speaker_correction(
        self,
        audio_segment_id: str,
        original_speaker: str,
        corrected_speaker: str,
        context: Optional[str] = None,
        run_id: Optional[str] = None,
        source_agent: str = "RosterReconcilerAgent",
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Hook for diarization and speaker corrections."""
        evt = self.adapter.build_speaker_correction_event(
            assigned_speaker=original_speaker,
            confirmed_speaker=corrected_speaker,
            turn_ids=[audio_segment_id] if audio_segment_id else None,
            source_agent=source_agent,
            run_id=run_id,
            receipt_id=receipt_id,
        )
        return self.record_outcome(evt)

    def record_decision_override(
        self,
        decision_id: str,
        recommended_option: Mapping[str, Any] | str,
        chosen_option: Mapping[str, Any] | str,
        category: str = "disambiguation",
        margin: float = 0.0,
        run_id: Optional[str] = None,
        source_agent: str = "HitlArbiterAgent",
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Hook for HITL decision overrides."""
        rec_str = recommended_option.get("option_id", str(recommended_option)) if isinstance(recommended_option, Mapping) else str(recommended_option)
        chosen_str = chosen_option.get("option_id", str(chosen_option)) if isinstance(chosen_option, Mapping) else str(chosen_option)
        evt = self.adapter.build_decision_override_event(
            decision_id=decision_id,
            category=category,
            recommended_option=rec_str,
            operator_chosen_option=chosen_str,
            margin_at_decision=margin,
            source_agent=source_agent,
            run_id=run_id,
            receipt_id=receipt_id,
        )
        return self.record_outcome(evt)

    def record_quality_failure(
        self,
        gate: str,
        dimension: str,
        failures: Sequence[str] | str,
        artifact_id: Optional[str] = None,
        run_id: Optional[str] = None,
        source_agent: str = "TranscriptQASwarm",
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Hook for artifact QA check failures."""
        fail_msg = "; ".join(failures) if isinstance(failures, (list, tuple)) else str(failures)
        evt = self.adapter.build_quality_failure_event(
            gate_name=gate,
            defect_dimension=dimension,
            failure_message=fail_msg,
            artifact_id=artifact_id,
            source_agent=source_agent,
            run_id=run_id,
            receipt_id=receipt_id,
        )
        return self.record_outcome(evt)

    def record_maintenance_finding(
        self,
        finding_type: str,
        entities: Sequence[str],
        details: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
        source_agent: str = "ContinuousMaintenanceSwarm",
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        """Hook for graph maintenance findings."""
        evt = self.adapter.build_maintenance_finding_event(
            finding_type=finding_type,
            entity_ids=list(entities),
            description=str(details) if details else finding_type,
            source_agent=source_agent,
            run_id=run_id,
            receipt_id=receipt_id,
        )
        return self.record_outcome(evt)

    def get_recorded_events(
        self,
        run_id: Optional[str] = None,
        outcome_type: Optional[OutcomeType | str] = None,
    ) -> List[AgentOutcomeEvent]:
        """Returns recorded events filtered by optional run_id and outcome_type."""
        target_type = outcome_type.value if isinstance(outcome_type, OutcomeType) else outcome_type
        with self._lock:
            events = list(self._events)
        if run_id:
            events = [e for e in events if e.run_id == run_id]
        if target_type:
            events = [e for e in events if e.outcome_type.value == target_type]
        return events

    def _drain_events_from_log(
        self,
        last_line: int,
        seen_ids: Set[str],
        new_seen_ids: List[str],
        last_byte_offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Reads unread lines from event log using O(1) byte-offset seek when valid, deduplicating against seen_ids."""
        cohort_items: List[Dict[str, Any]] = []
        current_line = last_line
        current_byte_offset = last_byte_offset
        if not self.events_log_path.is_file():
            return cohort_items, 0, 0
        try:
            file_size = self.events_log_path.stat().st_size
            with open(self.events_log_path, "rb") as fb:
                if 0 < last_byte_offset <= file_size:
                    fb.seek(last_byte_offset)
                else:
                    current_line = 0
                for raw_line in fb:
                    current_line += 1
                    if last_byte_offset == 0 and current_line <= last_line:
                        continue
                    evt = self._parse_log_line(raw_line.decode("utf-8", errors="replace"))
                    if evt is None or evt.event_id in seen_ids:
                        continue
                    cohort_items.append(self.adapter.to_cohort_item(evt))
                    seen_ids.add(evt.event_id)
                    new_seen_ids.append(evt.event_id)
                current_byte_offset = fb.tell()
        except OSError as err:
            logger.warning("Failed to read events log: %s", err)
        return cohort_items, current_line, current_byte_offset

    def _drain_fallback_events(
        self,
        seen_ids: Set[str],
        new_seen_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Drains unpersisted in-memory events if log was empty or unreadable."""
        cohort_items: List[Dict[str, Any]] = []
        for evt in self._events:
            if evt.event_id not in seen_ids:
                cohort_items.append(self.adapter.to_cohort_item(evt))
                seen_ids.add(evt.event_id)
                new_seen_ids.append(evt.event_id)
        return cohort_items

    def drain_to_cohort_items(self) -> List[Dict[str, Any]]:
        """Drains unread events from disk starting from cursor, enabling restart recovery."""
        with self._lock:
            cursor = self._load_cursor()
            last_line = int(cursor.get("last_processed_line", 0) or 0)
            last_byte_offset = int(cursor.get("last_processed_byte_offset", 0) or 0)
            seen_ids = set(cursor.get("processed_event_ids", []))
            new_seen_ids = list(cursor.get("processed_event_ids", []))

            cohort_items, current_line, current_byte_offset = self._drain_events_from_log(
                last_line, seen_ids, new_seen_ids, last_byte_offset=last_byte_offset
            )
            if not cohort_items and self._events:
                cohort_items.extend(self._drain_fallback_events(seen_ids, new_seen_ids))

            self._save_cursor(current_line, new_seen_ids, last_byte_offset=current_byte_offset)
            self._events.clear()
            return cohort_items

    def clear(self) -> None:
        """Resets the in-memory event buffer."""
        with self._lock:
            self._events.clear()


_GLOBAL_INTEGRATOR: Optional[LivePipelineIntegrator] = None
_GLOBAL_LOCK = threading.Lock()


def get_live_pipeline_integrator(runtime_root: Optional[Path] = None) -> LivePipelineIntegrator:
    """Returns singleton LivePipelineIntegrator instance."""
    global _GLOBAL_INTEGRATOR
    with _GLOBAL_LOCK:
        if _GLOBAL_INTEGRATOR is None:
            _GLOBAL_INTEGRATOR = LivePipelineIntegrator(runtime_root=runtime_root)
        return _GLOBAL_INTEGRATOR
