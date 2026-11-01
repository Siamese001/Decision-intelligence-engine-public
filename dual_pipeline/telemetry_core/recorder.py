"""
telemetry_core/recorder.py — Sensitive-content-safe spans and reconstructable learning events recorder.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import (
    POLICY_VERSION,
    assert_safe_mutable_path,
    atomic_write_bytes,
    digest,
    is_link_or_reparse,
    read_json,
    span_id,
    stable_id,
    utc_now,
)

from .constants import L2_OTEL_ATTRIBUTE_KEYS, SPAN_NAMES, ledger_connection


def _fast_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )


class RunRecorder:
    """Writes governed payloads, schema-valid learning events, and OTEL envelopes."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        run_id: str,
        trace_id: str,
        use_case: str,
        graph_snapshot: str | None,
        model_or_index_generation: str | None = None,
    ) -> None:
        if use_case not in SPAN_NAMES:
            raise ValueError(f"unsupported use case: {use_case}")
        supplied_runtime_root = Path(runtime_root).expanduser().absolute()
        if is_link_or_reparse(supplied_runtime_root):
            raise RuntimeError("runtime root cannot be a link or reparse point")
        self.runtime_root = supplied_runtime_root.resolve()
        self.run_id = run_id
        self.trace_id = trace_id
        self.use_case = use_case
        self.graph_snapshot = graph_snapshot
        self.model_or_index_generation = model_or_index_generation
        self.run_dir = self.runtime_root / "runs" / run_id
        self.payload_dir = self.run_dir / "ledger_payloads"
        self.event_dir = self.run_dir / "learning_events"
        self.otel_dir = self.run_dir / "otel"
        self.ledger_path = self.runtime_root / "ledger" / "learning.sqlite"
        self.otel_jsonl = self.runtime_root / "telemetry" / "otel.jsonl"
        self._assert_mutable_layout()
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self.event_dir.mkdir(parents=True, exist_ok=True)
        self.otel_dir.mkdir(parents=True, exist_ok=True)
        self._assert_mutable_layout()
        self._sequence = self._existing_event_count()
        self._parent_span_id = self._last_span_id()
        self._ledger_conn: Any = None
        self._pending_inserts = 0
        self._current_stage: str | None = None

    def _get_ledger_conn(self) -> Any:
        if self._ledger_conn is None:
            self._ledger_conn = ledger_connection(self.ledger_path)
        return self._ledger_conn

    def flush(self) -> None:
        if self._ledger_conn is not None and self._pending_inserts > 0:
            self._ledger_conn.commit()
            self._pending_inserts = 0

    def close(self) -> None:
        self.flush()
        if self._ledger_conn is not None:
            self._ledger_conn.close()
            self._ledger_conn = None

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _assert_mutable_layout(self) -> None:
        for target in (self.run_dir, self.payload_dir, self.event_dir, self.otel_dir, self.ledger_path, self.otel_jsonl):
            assert_safe_mutable_path(self.runtime_root, target, label="telemetry mutable path")

    def _sorted_event_paths(self) -> list[Path]:
        if not self.event_dir.exists():
            return []
        return sorted(self.event_dir.glob("*.json"), key=lambda p: (int(p.name.split("-", 1)[0]) if p.name.split("-", 1)[0].isdigit() else 0, p.name))

    def _existing_event_count(self) -> int:
        return len(self._sorted_event_paths())

    def _last_span_id(self) -> str | None:
        paths = self._sorted_event_paths()
        if not paths:
            return None
        return read_json(paths[-1]).get("span_id")

    def record(
        self,
        *,
        stage: str,
        event_type: str,
        payload: Any,
        correlation: dict[str, str | None],
        status: str = "PASS",
        route: str | None = None,
        lane: str | None = None,
        input_digest: str | None = None,
        record_count: int = 1,
        retry_count: int = 0,
        model_id: str | None = None,
        generation_id: str | None = None,
        graph_snapshot: str | None = None,
        otel_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._assert_mutable_layout()
        if stage not in SPAN_NAMES[self.use_case]:
            raise ValueError(f"unsupported {self.use_case} stage: {stage}")
        required_correlation = {
            "source_id",
            "candidate_id",
            "query_id",
            "graph_operation_id",
            "hitl_item_id",
        }
        if set(correlation) != required_correlation:
            raise ValueError("correlation must contain the five governed correlation keys")
        if not any(value is not None for value in correlation.values()):
            raise ValueError("at least one correlation value is required")
        extra_otel_attributes = dict(otel_attributes or {})
        unknown_otel_attributes = sorted(
            set(extra_otel_attributes) - L2_OTEL_ATTRIBUTE_KEYS
        )
        if unknown_otel_attributes:
            raise ValueError(
                "unsupported L2 OTEL attributes: "
                f"{unknown_otel_attributes}"
            )

        if self._current_stage != stage:
            self.flush()
            self._current_stage = stage

        self._sequence += 1
        timestamp = utc_now()
        payload_digest = digest(payload)
        event_id = stable_id(
            "event", self.run_id, self._sequence, stage, event_type, payload_digest, length=28
        )
        current_span_id = span_id(self.trace_id, event_id)
        payload_path = self.payload_dir / f"{self._sequence:04d}-{event_id}.json"
        event_path = self.event_dir / f"{self._sequence:04d}-{event_id}.json"
        otel_path = self.otel_dir / f"{self._sequence:04d}-{current_span_id}.json"
        _fast_write_json(payload_path, payload)

        learning_event = create_artifact(
            "learning-event",
            event_id,
            event_id=event_id,
            trace_id=self.trace_id,
            span_id=current_span_id,
            parent_span_id=self._parent_span_id,
            run_id=self.run_id,
            use_case=self.use_case,
            stage=stage,
            event_type=event_type,
            policy_version=POLICY_VERSION,
            graph_snapshot=graph_snapshot if graph_snapshot is not None else self.graph_snapshot,
            model_or_index_generation=(
                generation_id
                if generation_id is not None
                else self.model_or_index_generation
            ),
            input_digest=input_digest,
            output_digest=payload_digest,
            status=status,
            created_at=timestamp,
            correlation=correlation,
            payload_reference=f"run://{self.run_id}/ledger_payloads/{payload_path.name}",
            payload_digest=payload_digest,
            extensions={},
        )
        _fast_write_json(event_path, learning_event)

        attributes = {
            "run_id": self.run_id,
            "use_case": self.use_case,
            "stage": stage,
            "route": route,
            "lane": lane,
            "decision_status": status,
            "model_id": model_id,
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot if graph_snapshot is not None else self.graph_snapshot,
            "input_digest": input_digest,
            "output_digest": payload_digest,
            "record_count": record_count,
            "retry_count": retry_count,
            **extra_otel_attributes,
        }
        otel = create_artifact(
            "otel-event-envelope",
            stable_id("otel", event_id, length=28),
            trace_id=self.trace_id,
            span_id=current_span_id,
            parent_span_id=self._parent_span_id,
            span_name=SPAN_NAMES[self.use_case][stage],
            started_at=timestamp,
            ended_at=timestamp,
            status="ERROR" if status in {"FAIL", "BLOCKED"} else "OK",
            attributes=attributes,
            extensions={},
        )
        _fast_write_json(otel_path, otel)
        self.otel_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with self.otel_jsonl.open("ab") as stream:
            stream.write((json.dumps(otel, sort_keys=True) + "\n").encode("utf-8"))

        connection = self._get_ledger_conn()
        connection.execute(
            """INSERT OR REPLACE INTO learning_event
               (event_id,run_id,trace_id,span_id,parent_span_id,use_case,stage,event_type,
                status,payload_reference,payload_digest,artifact_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                self.run_id,
                self.trace_id,
                current_span_id,
                self._parent_span_id,
                self.use_case,
                stage,
                event_type,
                status,
                learning_event["payload_reference"],
                payload_digest,
                json.dumps(learning_event, sort_keys=True),
                timestamp,
            ),
        )
        self._pending_inserts += 1
        if self._pending_inserts >= 25:
            self.flush()
        self._parent_span_id = current_span_id
        return learning_event

    def seal(
        self,
        required_stages: Iterable[str],
        correlation: dict[str, str | None],
        *,
        status: str = "PASS",
    ) -> dict[str, Any]:
        self.flush()
        self._assert_mutable_layout()
        required = list(dict.fromkeys(required_stages))
        preseal = self.audit(required_stages=[stage for stage in required if stage != "L4"])
        self.record(
            stage="L4",
            event_type="pipeline_event",
            payload={
                "run_id": self.run_id,
                "preseal_event_count": preseal["event_count"],
                "preseal_event_chain_digest": preseal["event_chain_digest"],
                "required_stages": required,
                "automatic_policy_change_count": 0,
            },
            correlation=correlation,
            status=status,
            record_count=preseal["event_count"],
        )
        result = self.audit(required_stages=required)
        connection = self._get_ledger_conn()
        connection.execute(
            """INSERT OR REPLACE INTO run_seal
               (run_id,trace_id,use_case,status,required_stages_json,observed_stages_json,
                event_count,event_chain_digest,sealed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                self.run_id,
                self.trace_id,
                self.use_case,
                status,
                json.dumps(required),
                json.dumps(result["observed_stages"]),
                result["event_count"],
                result["event_chain_digest"],
                utc_now(),
            ),
        )
        connection.commit()
        self._pending_inserts = 0
        _fast_write_json(self.run_dir / "run_seal.json", result)
        return result

    def audit(
        self,
        required_stages: Iterable[str] = (),
        *,
        event_limit: int | None = None,
    ) -> dict[str, Any]:
        self.flush()
        self._assert_mutable_layout()
        event_paths = self._sorted_event_paths()
        if not event_paths:
            raise ValueError(f"run has no learning events: {self.run_id}")
        if event_limit is not None:
            if event_limit < 1 or len(event_paths) < event_limit:
                raise ValueError("learning ledger does not contain the requested prefix")
            event_paths = event_paths[:event_limit]
        events: list[dict[str, Any]] = []
        previous_span: str | None = None
        for event_path in event_paths:
            event = read_json(event_path)
            validate_artifact(event)
            if event["run_id"] != self.run_id or event["trace_id"] != self.trace_id:
                raise ValueError("run or trace correlation mismatch")
            if event["parent_span_id"] != previous_span:
                raise ValueError(f"broken span chain at {event['event_id']}")
            payload_name = event["payload_reference"].rsplit("/", 1)[-1]
            payload_path = self.payload_dir / payload_name
            if not payload_path.is_file() or digest(read_json(payload_path)) != event["payload_digest"]:
                raise ValueError(f"payload digest mismatch for {event['event_id']}")
            events.append(event)
            previous_span = event["span_id"]
        observed = list(dict.fromkeys(event["stage"] for event in events))
        missing = sorted(set(required_stages) - set(observed))
        if missing:
            raise ValueError(f"learning ledger is missing required stages: {missing}")
        chain_digest = digest(
            [
                {
                    "event_id": event["event_id"],
                    "canonical_digest": event["canonical_digest"]["value"],
                    "payload_digest": event["payload_digest"],
                }
                for event in events
            ]
        )
        return {
            "status": "PASS",
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "use_case": self.use_case,
            "event_count": len(events),
            "observed_stages": observed,
            "required_stages": list(required_stages),
            "missing_stages": [],
            "event_chain_digest": chain_digest,
            "automatic_policy_change_count": 0,
        }


def verify_run_l4_seal(runtime_root: Path | str, run_id: str) -> dict[str, Any]:
    """Verifies that an execution run possesses an authentic, untampered L4 seal."""
    root = Path(runtime_root).resolve()
    run_dir = root / "runs" / run_id
    seal_file = run_dir / "run_seal.json"
    if not seal_file.is_file():
        raise ValueError(f"No run_seal.json found for run '{run_id}' in {run_dir}")
    seal_data = read_json(seal_file)

    # Check database record in learning.sqlite if present
    db_path = root / "ledger" / "learning.sqlite"
    if db_path.is_file():
        conn = ledger_connection(db_path)
        try:
            row = conn.execute(
                "SELECT run_id, trace_id, use_case, status, required_stages_json, event_chain_digest FROM run_seal WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Run seal not found in database for run '{run_id}'")
            if row["event_chain_digest"] != seal_data["event_chain_digest"]:
                raise ValueError("Database seal digest does not match file seal digest (tampering detected)")
        finally:
            conn.close()

    # Re-audit event chain
    recorder = RunRecorder(
        runtime_root=root,
        run_id=run_id,
        trace_id=seal_data["trace_id"],
        use_case=seal_data["use_case"],
        graph_snapshot=None,
    )
    try:
        try:
            audit_res = recorder.audit(required_stages=seal_data.get("required_stages", ()))
            if audit_res["event_chain_digest"] != seal_data["event_chain_digest"]:
                raise ValueError("Recomputed event chain digest does not match sealed digest (tampering detected)")
        except Exception as exc:
            raise ValueError(f"Event chain audit failed (tampering detected): {exc}") from exc
    finally:
        recorder.close()

    return {
        "status": "PASS",
        "run_id": run_id,
        "seal_verified": True,
        "event_chain_digest": seal_data["event_chain_digest"],
        "observed_stages": seal_data.get("observed_stages", []),
    }

