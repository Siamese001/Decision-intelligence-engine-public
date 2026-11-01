"""
dual_pipeline/l0/attempt_ledger.py

Append-only attempt ledger for request-level lifecycle tracking.
Preserves cross-attempt lineage across retries, repairs, and rollbacks.
Complies with Layer 0 architectural invariants (zero upward imports).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


@dataclass
class AttemptRecord:
    attempt_id: int
    parent_attempt_id: Optional[int]
    request_id: str
    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    started_tick: int = field(default_factory=time.perf_counter_ns)
    duration_ms: Optional[float] = None
    status: str = "INCOMPLETE"  # INCOMPLETE, PASS, FAIL, FAILED
    terminal_outcome: str = "INCOMPLETE"  # COMPLETE, FAILED, INCOMPLETE
    retry_cause: Optional[str] = None
    input_digest: str = ""
    artifact_digest: str = ""
    model_digest: str = ""
    code_digest: str = ""
    policy_digest: str = ""
    resume_decision: Optional[Dict[str, Any]] = None
    provider_calls: List[Dict[str, Any]] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("started_tick", None)
        return data


class AttemptLedger:
    """Append-only attempt ledger persisted to disk and memory per request."""

    def __init__(
        self,
        request_id: str,
        runtime_root: Optional[Path] = None,
        storage_dir: Optional[Path] = None,
    ):
        self.request_id = request_id
        effective_root = runtime_root or storage_dir
        self.runtime_root = effective_root
        self._attempts: List[AttemptRecord] = []
        self._active_attempts: Dict[int, AttemptRecord] = {}
        if effective_root is not None:
            self._ledger_path = effective_root / "requests" / request_id / "attempt_ledger.jsonl"
            self._load_from_disk()
        else:
            self._ledger_path = None

    @property
    def ledger_path(self) -> Optional[Path]:
        return self._ledger_path

    def _load_from_disk(self) -> None:
        if self._ledger_path and self._ledger_path.is_file():
            try:
                with self._ledger_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            data = json.loads(line)
                            record = AttemptRecord(
                                attempt_id=int(data["attempt_id"]),
                                parent_attempt_id=int(data["parent_attempt_id"]) if data.get("parent_attempt_id") is not None else None,
                                request_id=data.get("request_id", self.request_id),
                                run_id=data.get("run_id", ""),
                                started_at=data.get("started_at", ""),
                                finished_at=data.get("finished_at"),
                                duration_ms=data.get("duration_ms"),
                                status=data.get("status", "INCOMPLETE"),
                                terminal_outcome=data.get("terminal_outcome", "INCOMPLETE"),
                                retry_cause=data.get("retry_cause"),
                                input_digest=data.get("input_digest", ""),
                                artifact_digest=data.get("artifact_digest", ""),
                                model_digest=data.get("model_digest", ""),
                                code_digest=data.get("code_digest", ""),
                                policy_digest=data.get("policy_digest", ""),
                                resume_decision=data.get("resume_decision"),
                                provider_calls=data.get("provider_calls", []),
                                attributes=data.get("attributes", {}),
                            )
                            self._attempts.append(record)
            except Exception:
                pass

    def _append_to_disk(self, record: AttemptRecord) -> None:
        if self._ledger_path is None:
            return
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.to_dict()
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._ledger_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def start_attempt(
        self,
        attempt_id: int,
        run_id: str,
        parent_attempt_id: Optional[int] = None,
        input_digest: str = "",
        model_digest: str = "",
        code_digest: str = "",
        policy_digest: str = "",
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> AttemptRecord:
        record = AttemptRecord(
            attempt_id=attempt_id,
            parent_attempt_id=parent_attempt_id if parent_attempt_id is not None else (attempt_id - 1 if attempt_id > 1 else None),
            request_id=self.request_id,
            run_id=run_id,
            started_at=_utc_now_iso(),
            started_tick=time.perf_counter_ns(),
            status="INCOMPLETE",
            terminal_outcome="INCOMPLETE",
            input_digest=input_digest,
            model_digest=model_digest,
            code_digest=code_digest,
            policy_digest=policy_digest,
            attributes=dict(attributes or {}),
        )
        self._active_attempts[attempt_id] = record
        return record

    def record_provider_call(
        self,
        attempt_id: int,
        provider_call: Mapping[str, Any],
    ) -> None:
        call_entry = {
            "call_id": uuid4().hex,
            "recorded_at": _utc_now_iso(),
            **dict(provider_call),
        }
        if attempt_id in self._active_attempts:
            self._active_attempts[attempt_id].provider_calls.append(call_entry)
        for att in self._attempts:
            if att.attempt_id == attempt_id:
                att.provider_calls.append(call_entry)

    def finish_attempt(
        self,
        attempt_id: int,
        status: str,
        terminal_outcome: str = "COMPLETE",
        retry_cause: Optional[str] = None,
        artifact_digest: str = "",
        resume_decision: Optional[Mapping[str, Any]] = None,
        extra_attributes: Optional[Mapping[str, Any]] = None,
    ) -> AttemptRecord:
        record = self._active_attempts.pop(attempt_id, None)
        now_tick = time.perf_counter_ns()
        now_iso = _utc_now_iso()
        if record is None:
            record = AttemptRecord(
                attempt_id=attempt_id,
                parent_attempt_id=attempt_id - 1 if attempt_id > 1 else None,
                request_id=self.request_id,
                run_id="",
                started_at=now_iso,
                started_tick=now_tick,
            )
        record.finished_at = now_iso
        record.duration_ms = round((now_tick - record.started_tick) / 1e6, 3)
        record.status = status
        record.terminal_outcome = terminal_outcome
        if retry_cause:
            record.retry_cause = retry_cause
        if artifact_digest:
            record.artifact_digest = artifact_digest
        if resume_decision:
            record.resume_decision = dict(resume_decision)
        if extra_attributes:
            record.attributes.update(dict(extra_attributes))

        # Check if record already exists in self._attempts
        existing_idx = next((i for i, a in enumerate(self._attempts) if a.attempt_id == attempt_id), None)
        if existing_idx is not None:
            self._attempts[existing_idx] = record
        else:
            self._attempts.append(record)

        self._append_to_disk(record)
        return record

    def get_attempts(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._attempts]

    def export_to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request_id": self.request_id,
            "total_attempts": len(self._attempts),
            "attempts": self.get_attempts(),
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())


# Module-level registry for active request ledgers
_REQUEST_LEDGERS: Dict[str, AttemptLedger] = {}


def get_or_create_attempt_ledger(request_id: str, runtime_root: Optional[Path] = None) -> AttemptLedger:
    if request_id not in _REQUEST_LEDGERS or (
        runtime_root is not None and _REQUEST_LEDGERS[request_id].runtime_root is None
    ):
        _REQUEST_LEDGERS[request_id] = AttemptLedger(request_id, runtime_root)
    return _REQUEST_LEDGERS[request_id]
